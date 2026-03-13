"""Memory service for agent episodic and semantic memory."""
from typing import List, Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from datetime import datetime, timedelta
import math
import json
import structlog

from app.models import (
    AgentEpisodicMemory,
    AgentSemanticMemory,
    AgentWorkingMemory,
    Message
)
from app.services.llm_router import llm_router
from app.services.embedding_service import embedding_service
from app.config import settings

logger = structlog.get_logger()

# Per-type scoring weights. Lambdas come from settings; weights are less likely to need env-level tuning.
_SCORE_WEIGHTS = {
    "fact_state":             {"sim": 0.65, "imp": 0.20, "fresh": 0.15},
    "fact":                   {"sim": 0.65, "imp": 0.20, "fresh": 0.15},  # legacy type
    "observation_preference": {"sim": 0.65, "imp": 0.15, "act": 0.20},
    "observation":            {"sim": 0.65, "imp": 0.15, "act": 0.20},   # legacy type
    "immutable":              {"sim": 0.75, "imp": 0.25},
}

# Temporal indicator phrases for time-aware query detection
_TEMPORAL_INDICATORS = [
    "last week", "last month", "last year", "last quarter",
    "in january", "in february", "in march", "in april", "in may", "in june",
    "in july", "in august", "in september", "in october", "in november", "in december",
    "in 2023", "in 2024", "in 2025", "in 2026",
    "before launch", "after launch", "before v2", "after v2",
    "when we", "at the time", "as of", "back when",
    "yesterday", "two weeks ago", "a month ago", "six months ago",
    "two months ago", "three months ago",
]


class MemoryService:
    """Service for managing agent memories."""

    # ------------------------------------------------------------------
    # Scoring helpers
    # ------------------------------------------------------------------

    def _compute_memory_score(
        self,
        memory: AgentEpisodicMemory,
        similarity: float,
        now: datetime,
    ) -> float:
        """Compute a temporal-aware relevance score for a memory.

        Immutable types (decisions, rejections, events) receive no freshness
        penalty — they are permanent records and should surface at full weight
        regardless of age. Mutable state facts decay with age; preferences decay
        based on access recency.
        """
        norm_importance = (memory.importance or 5) / settings.memory_importance_max

        if memory.memory_type in settings.memory_immutable_types:
            w = _SCORE_WEIGHTS["immutable"]
            return w["sim"] * similarity + w["imp"] * norm_importance

        if memory.memory_type in ("fact_state", "fact"):
            w = _SCORE_WEIGHTS["fact_state"]
            age_days = max(0, (now - (memory.valid_from or memory.created_at)).days)
            freshness = math.exp(-settings.memory_lambda_state * age_days)
            return w["sim"] * similarity + w["imp"] * norm_importance + w["fresh"] * freshness

        # observation_preference, observation
        w = _SCORE_WEIGHTS["observation_preference"]
        last_access = memory.last_accessed_at or memory.created_at
        days_since_access = max(0, (now - last_access).days)
        recency_boost = math.exp(-settings.memory_lambda_preference * days_since_access)
        raw_activation = math.log(1 + (memory.access_count or 0)) * recency_boost
        norm_activation = min(raw_activation, 5.0) / 5.0
        return w["sim"] * similarity + w["imp"] * norm_importance + w["act"] * norm_activation

    # ------------------------------------------------------------------
    # Temporal query helpers
    # ------------------------------------------------------------------

    def _detect_temporal_query(self, query: str) -> bool:
        """Return True if the query contains temporal reference language."""
        query_lower = query.lower()
        return any(phrase in query_lower for phrase in _TEMPORAL_INDICATORS)

    async def _extract_time_range(self, query: str) -> dict:
        """Use Haiku to extract a structured time range from a temporal query.

        Returns a dict with optional 'after' and 'before' ISO date strings,
        or {} if no range could be extracted or on any error.
        """
        try:
            response = await llm_router.generate_response(
                system_prompt="Extract time ranges from queries as JSON. Return only valid JSON.",
                messages=[{
                    "role": "user",
                    "content": (
                        "Extract the time range implied by this query. "
                        "Return a JSON object with optional 'after' and 'before' keys, "
                        "each an ISO 8601 date string (YYYY-MM-DD). "
                        "If no clear time range is present, return {}.\n\n"
                        f"Query: {query}\n\nJSON:"
                    ),
                }],
                model="claude-haiku-4-5-20251001",
                max_tokens=100,
                temperature=0.0,
            )
            content = response["content"].strip()
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
            return json.loads(content)
        except Exception as e:
            logger.warning("time_range_extraction_failed", error=str(e))
            return {}

    # ------------------------------------------------------------------
    # Core memory operations
    # ------------------------------------------------------------------

    async def store_episodic_memory(
        self,
        agent_id: str,
        content: str,
        memory_type: str,
        source_message_id: Optional[str],
        source_conversation_id: Optional[str],
        db: AsyncSession,
        confidence: float = 1.0,
        attribute_key: Optional[str] = None,
        importance: Optional[int] = None,
        keywords: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        valid_from: Optional[datetime] = None,
        occurred_at: Optional[datetime] = None,
    ) -> AgentEpisodicMemory:
        """Store a new episodic memory, applying supersession or dedup as appropriate.

        Immutable types (event, decision, rejection, reflection_summary) bypass all
        deduplication and are always stored as distinct records.

        Mutable types (fact_state, observation_preference and their legacy equivalents)
        first check for attribute-key-based supersession, then fall back to
        similarity-based dedup with an optional Haiku tiebreaker.
        """
        now = datetime.utcnow()

        # Generate embedding
        try:
            embedding = await embedding_service.generate_embedding(content)
        except Exception as e:
            logger.error("embedding_generation_failed", error=str(e))
            embedding = None

        # Build extra_data for structured attributes (no redundant importance key)
        extra_data: Optional[dict] = None
        attrs = {}
        if attribute_key:
            attrs["attribute_key"] = attribute_key
        if keywords:
            attrs["keywords"] = keywords
        if tags:
            attrs["tags"] = tags
        if attrs:
            extra_data = attrs

        # ------------------------------------------------------------------
        # IMMUTABLE TYPES: skip dedup, store directly
        # ------------------------------------------------------------------
        if memory_type in settings.memory_immutable_types:
            memory = AgentEpisodicMemory(
                agent_id=agent_id,
                memory_type=memory_type,
                content=content,
                source_message_id=source_message_id,
                source_conversation_id=source_conversation_id,
                confidence=confidence,
                embedding=embedding,
                importance=importance,
                valid_from=valid_from or now,
                asserted_at=now,
                occurred_at=occurred_at,
                extra_data=extra_data,
            )
            db.add(memory)
            await db.flush()
            logger.info(
                "episodic_memory_stored",
                agent_id=agent_id,
                memory_id=memory.id,
                type=memory_type,
                importance=importance,
            )
            return memory

        # ------------------------------------------------------------------
        # MUTABLE TYPES — Path 1: attribute_key supersession
        # ------------------------------------------------------------------
        if attribute_key and embedding:
            active_result = await db.execute(
                select(AgentEpisodicMemory)
                .where(AgentEpisodicMemory.agent_id == agent_id)
                .where(AgentEpisodicMemory.valid_until.is_(None))
                .where(AgentEpisodicMemory.memory_type.in_(settings.memory_mutable_types))
            )
            active_memories = active_result.scalars().all()

            existing_with_key = [
                m for m in active_memories
                if m.extra_data and m.extra_data.get("attribute_key") == attribute_key
            ]

            if existing_with_key:
                supersede_time = valid_from or now
                # Create the new memory first so we have its id for superseded_by
                new_memory = AgentEpisodicMemory(
                    agent_id=agent_id,
                    memory_type=memory_type,
                    content=content,
                    source_message_id=source_message_id,
                    source_conversation_id=source_conversation_id,
                    confidence=confidence,
                    embedding=embedding,
                    importance=importance,
                    valid_from=valid_from or now,
                    asserted_at=now,
                    occurred_at=occurred_at,
                    extra_data=extra_data,
                )
                db.add(new_memory)
                await db.flush()

                for old in existing_with_key:
                    old.valid_until = supersede_time
                    old.asserted_until = now
                    old.superseded_by = new_memory.id
                await db.flush()

                logger.info(
                    "episodic_memory_superseded_by_attribute_key",
                    agent_id=agent_id,
                    attribute_key=attribute_key,
                    superseded_count=len(existing_with_key),
                    new_memory_id=new_memory.id,
                )
                return new_memory

        # ------------------------------------------------------------------
        # MUTABLE TYPES — Path 2: similarity-based dedup / Haiku tiebreaker
        # ------------------------------------------------------------------
        if embedding:
            recent_result = await db.execute(
                select(AgentEpisodicMemory)
                .where(AgentEpisodicMemory.agent_id == agent_id)
                .where(AgentEpisodicMemory.valid_until.is_(None))
                .where(AgentEpisodicMemory.embedding.isnot(None))
                .order_by(AgentEpisodicMemory.created_at.desc())
                .limit(20)
            )
            recent_memories = recent_result.scalars().all()

            for existing in recent_memories:
                similarity = embedding_service.cosine_similarity(embedding, existing.embedding)

                if similarity > 0.92:
                    # Very high similarity = exact duplicate; discard
                    logger.info(
                        "memory_deduplicated",
                        agent_id=agent_id,
                        content_preview=content[:80],
                        similar_to=existing.content[:80],
                        similarity=round(similarity, 3),
                    )
                    return existing

                if similarity > settings.memory_supersession_similarity_threshold:
                    # Ambiguous zone: ask Haiku whether this is an update or a repeat
                    try:
                        verdict_response = await llm_router.generate_response(
                            system_prompt="You compare two memory statements. Answer with only UPDATE or SAME.",
                            messages=[{
                                "role": "user",
                                "content": (
                                    f"Memory A: {existing.content}\n"
                                    f"Memory B: {content}\n\n"
                                    "Does Memory B update or contradict Memory A, "
                                    "or is it saying the same thing?\n"
                                    "Answer UPDATE or SAME:"
                                ),
                            }],
                            model="claude-haiku-4-5-20251001",
                            max_tokens=10,
                            temperature=0.0,
                        )
                        verdict = verdict_response["content"].strip().upper()

                        if "UPDATE" in verdict:
                            supersede_time = valid_from or now
                            new_memory = AgentEpisodicMemory(
                                agent_id=agent_id,
                                memory_type=memory_type,
                                content=content,
                                source_message_id=source_message_id,
                                source_conversation_id=source_conversation_id,
                                confidence=confidence,
                                embedding=embedding,
                                importance=importance,
                                valid_from=valid_from or now,
                                asserted_at=now,
                                occurred_at=occurred_at,
                                extra_data=extra_data,
                            )
                            db.add(new_memory)
                            await db.flush()
                            existing.valid_until = supersede_time
                            existing.asserted_until = now
                            existing.superseded_by = new_memory.id
                            await db.flush()
                            logger.info(
                                "episodic_memory_superseded_by_llm",
                                agent_id=agent_id,
                                new_memory_id=new_memory.id,
                                similarity=round(similarity, 3),
                            )
                            return new_memory
                        else:
                            logger.info(
                                "memory_deduplicated_by_llm",
                                agent_id=agent_id,
                                similarity=round(similarity, 3),
                            )
                            return existing

                    except Exception as e:
                        logger.warning("supersession_llm_check_failed", error=str(e))
                        # Fall through to normal store on error

        # ------------------------------------------------------------------
        # No supersession match: store as new memory
        # ------------------------------------------------------------------
        memory = AgentEpisodicMemory(
            agent_id=agent_id,
            memory_type=memory_type,
            content=content,
            source_message_id=source_message_id,
            source_conversation_id=source_conversation_id,
            confidence=confidence,
            embedding=embedding,
            importance=importance,
            valid_from=valid_from or now,
            asserted_at=now,
            occurred_at=occurred_at,
            extra_data=extra_data,
        )
        db.add(memory)
        await db.flush()

        logger.info(
            "episodic_memory_stored",
            agent_id=agent_id,
            memory_id=memory.id,
            type=memory_type,
            attribute_key=attribute_key,
            importance=importance,
            has_embedding=embedding is not None,
        )
        return memory

    async def consolidate_episodic_memories(
        self,
        agent_id: str,
        db: AsyncSession,
        similarity_threshold: float = 0.85,
        min_memories_for_consolidation: int = 3
    ) -> List[AgentSemanticMemory]:
        """Consolidate similar mutable episodic memories into semantic memory.

        Only mutable types participate in clustering. Immutable types (decisions,
        rejections, events) are never consolidated — they are discrete historical
        records. Clusters are sorted chronologically before being passed to the LLM
        so the consolidation reflects the most recent state.
        """
        # Only cluster mutable, active memories
        result = await db.execute(
            select(AgentEpisodicMemory)
            .where(AgentEpisodicMemory.agent_id == agent_id)
            .where(AgentEpisodicMemory.valid_until.is_(None))
            .where(AgentEpisodicMemory.embedding.isnot(None))
            .where(AgentEpisodicMemory.memory_type.in_(settings.memory_mutable_types))
            .order_by(AgentEpisodicMemory.created_at.desc())
            .limit(100)
        )
        memories = result.scalars().all()

        if len(memories) < min_memories_for_consolidation:
            return []

        clusters = []
        processed = set()

        for i, mem1 in enumerate(memories):
            if mem1.id in processed:
                continue
            cluster = [mem1]
            processed.add(mem1.id)

            for mem2 in memories[i + 1:]:
                if mem2.id in processed:
                    continue
                similarity = embedding_service.cosine_similarity(mem1.embedding, mem2.embedding)
                if similarity >= similarity_threshold:
                    cluster.append(mem2)
                    processed.add(mem2.id)

            if len(cluster) >= min_memories_for_consolidation:
                clusters.append(cluster)

        if not clusters:
            logger.info("no_clusters_for_consolidation", agent_id=agent_id)
            return []

        created_semantic_memories = []
        now = datetime.utcnow()

        def _relative_time(dt: datetime) -> str:
            days = max(0, (now - dt).days)
            if days < 1:
                return "today"
            if days < 7:
                return f"{days}d ago"
            if days < 30:
                return f"{days // 7}w ago"
            return f"{days // 30}mo ago"

        for cluster in clusters:
            try:
                # Sort oldest-to-newest so the LLM synthesizes toward recent state
                cluster_sorted = sorted(
                    cluster,
                    key=lambda m: m.valid_from or m.created_at
                )
                memory_texts = "\n".join([
                    f"[{_relative_time(m.valid_from or m.created_at)}] {m.content}"
                    for m in cluster_sorted
                ])

                consolidation_prompt = f"""You are consolidating similar episodic memories into semantic knowledge.

Memories are listed oldest to newest. The most recent memories are more likely to reflect the current state.
If entries represent a state change, describe the current state and note the transition.

Episodic memories:
{memory_texts}

Extract consolidated semantic knowledge from these memories. Return a JSON object with:
- category: one of ["preference", "rule", "concept", "relationship"]
- key: short identifier (2-5 words, lowercase_with_underscores)
- value: consolidated statement (1-2 sentences, reflecting most recent state)

Return ONLY the JSON object, nothing else."""

                response = await llm_router.generate_response(
                    system_prompt="You are a memory consolidation system. Extract semantic knowledge as structured JSON.",
                    messages=[{"role": "user", "content": consolidation_prompt}],
                    model=settings.default_claude_model,
                    max_tokens=200,
                )

                content = response["content"].strip()
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0].strip()

                consolidated_data = json.loads(content)
                category = consolidated_data.get("category", "concept")
                key = consolidated_data.get("key", "unknown")
                value = consolidated_data.get("value", "")

                # Carry forward attribute_keys from source memories for traceability
                source_keys = list({
                    m.extra_data.get("attribute_key")
                    for m in cluster
                    if m.extra_data and m.extra_data.get("attribute_key")
                })

                existing_result = await db.execute(
                    select(AgentSemanticMemory)
                    .where(AgentSemanticMemory.agent_id == agent_id)
                    .where(AgentSemanticMemory.category == category)
                    .where(AgentSemanticMemory.key == key)
                )
                existing_semantic = existing_result.scalar_one_or_none()

                if existing_semantic:
                    existing_semantic.value = value
                    existing_semantic.source_count += len(cluster)
                    existing_semantic.updated_at = now
                    if source_keys:
                        existing_semantic.extra_data = {"attribute_keys": source_keys}
                    semantic_memory = existing_semantic
                else:
                    semantic_memory = AgentSemanticMemory(
                        agent_id=agent_id,
                        category=category,
                        key=key,
                        value=value,
                        source_count=len(cluster),
                        confidence=0.9,
                        extra_data={"attribute_keys": source_keys} if source_keys else None,
                    )
                    db.add(semantic_memory)

                await db.flush()

                # Archive source episodic memories (set both confidence and valid_until
                # for backwards compatibility with any code still checking confidence > 0)
                for mem in cluster:
                    mem.confidence = 0.0
                    mem.valid_until = now
                    mem.asserted_until = now

                await db.flush()
                created_semantic_memories.append(semantic_memory)

                logger.info(
                    "memories_consolidated",
                    agent_id=agent_id,
                    category=category,
                    key=key,
                    source_count=len(cluster),
                    semantic_id=semantic_memory.id,
                )

            except Exception as e:
                logger.error(
                    "consolidation_error",
                    agent_id=agent_id,
                    cluster_size=len(cluster),
                    error=str(e),
                )
                continue

        return created_semantic_memories

    async def extract_memories_from_conversation(
        self,
        agent_id: str,
        conversation_id: str,
        recent_message_count: int,
        db: AsyncSession
    ) -> List[str]:
        """Extract and store memorable facts from recent conversation.

        Uses the LLM to classify memories by type at write time. Returns a list
        of stored content strings (for logging/testing).
        """
        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(recent_message_count)
        )
        messages = list(reversed(result.scalars().all()))

        if not messages:
            return []

        human_messages = [msg for msg in messages if msg.sender_type == "human"]
        if not human_messages:
            return []

        conversation_text = "\n".join([f"User: {msg.content}" for msg in human_messages])
        total_chars = sum(len(msg.content) for msg in human_messages)
        if total_chars < 100:
            logger.info(
                "skipping_memory_extraction_insufficient_content",
                agent_id=agent_id,
                char_count=total_chars,
            )
            return []

        extraction_prompt = """You are extracting memorable facts from a conversation to store in an agent's long-term memory.

MEMORY TYPES:
- fact_state: A current-state claim about the project or world that may change over time.
  Example: "The backend uses FastAPI." / "The project is in beta with 12 customers."
- observation_preference: A pattern or preference about how the user works.
  Example: "The user prefers written specs before implementation begins."
- event: Something that happened at a specific point in time (immutable historical fact).
  Example: "Version 2 launched on March 15." / "The team moved to remote-first in Q1."
- decision: A deliberate choice made, with reasoning. Store the reasoning too.
  Example: "Decided to use SQLite over Postgres — simplicity is the priority at this scale."
- rejection: An idea that was explicitly ruled out, with reasoning.
  Example: "Ruled out microservices. Team too small to maintain separate services."

RULES:
- Only extract actual facts or decisions the user PROVIDED — not questions or requests.
- Decisions and rejections are the most valuable type. Capture the reasoning, not just the conclusion.
- For fact_state, assign an attribute_key: a 2-4 word snake_case identifier for what the fact is about.
  Examples: "backend_framework", "project_phase", "team_size", "primary_language"
- Rate importance 1-10 (10 = critical architectural decision; 1 = minor detail).
- If you cannot find 2-3 distinct memorable facts, return an empty array.
- Return ONLY a JSON array. No explanation, no markdown fence, just the array.

Each item must have this shape:
{{"type": "<type>", "content": "<clear standalone statement>", "attribute_key": "<snake_case or null>", "importance": <1-10>, "keywords": ["<kw1>"], "tags": ["<tag1>"], "occurred_at": "<ISO date or null>"}}

Conversation:
{conversation}

JSON array:"""

        try:
            response = await llm_router.generate_response(
                system_prompt="You are a memory extraction system. Return only valid JSON arrays.",
                messages=[{
                    "role": "user",
                    "content": extraction_prompt.format(conversation=conversation_text),
                }],
                model=settings.default_claude_model,
                max_tokens=800,
            )

            content = response["content"].strip()
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            memories_data = json.loads(content)
            if not isinstance(memories_data, list):
                memories_data = []

            stored_contents = []
            for item in memories_data[:3]:
                if not isinstance(item, dict) or not item.get("content"):
                    continue

                occurred_at = None
                if item.get("occurred_at"):
                    try:
                        occurred_at = datetime.fromisoformat(
                            item["occurred_at"].replace("Z", "+00:00")
                        )
                    except (ValueError, AttributeError):
                        occurred_at = None

                await self.store_episodic_memory(
                    agent_id=agent_id,
                    content=item["content"],
                    memory_type=item.get("type", "fact_state"),
                    source_message_id=messages[-1].id if messages else None,
                    source_conversation_id=conversation_id,
                    db=db,
                    attribute_key=item.get("attribute_key"),
                    importance=item.get("importance"),
                    keywords=item.get("keywords"),
                    tags=item.get("tags"),
                    occurred_at=occurred_at,
                )
                stored_contents.append(item["content"])

            logger.info("memories_extracted", agent_id=agent_id, count=len(stored_contents))

            # Trigger consolidation based on active mutable memory count
            total_result = await db.execute(
                select(AgentEpisodicMemory)
                .where(AgentEpisodicMemory.agent_id == agent_id)
                .where(AgentEpisodicMemory.valid_until.is_(None))
                .where(AgentEpisodicMemory.memory_type.in_(settings.memory_mutable_types))
            )
            total_mutable = len(total_result.scalars().all())

            if (
                total_mutable >= settings.consolidation_threshold
                and total_mutable % settings.consolidation_threshold == 0
            ):
                logger.info(
                    "triggering_consolidation",
                    agent_id=agent_id,
                    total_mutable=total_mutable,
                )
                try:
                    await self.consolidate_episodic_memories(agent_id=agent_id, db=db)
                except Exception as e:
                    logger.error("consolidation_trigger_error", agent_id=agent_id, error=str(e))

            return stored_contents

        except (json.JSONDecodeError, Exception) as e:
            logger.error("memory_extraction_error", error=str(e))
            return []

    async def retrieve_relevant_memories(
        self,
        agent_id: str,
        query: str,
        db: AsyncSession,
        conversation_id: Optional[str] = None,
        limit: int = 5,
        time_window_days: Optional[int] = None,
        mode: str = "current",
    ) -> List[AgentEpisodicMemory]:
        """Retrieve relevant episodic memories from ALL conversations.

        Uses type-conditional scoring: immutable memories (decisions, rejections, events)
        are ranked by semantic similarity + importance only — no age penalty.
        Mutable state facts receive a freshness discount; preferences use access activation.

        The `mode` parameter controls temporal filtering:
          - "current" (default): only active memories (valid_until IS NULL)
          - "as_of", "range", "timeline": not yet implemented; raises NotImplementedError
        """
        if mode != "current":
            raise NotImplementedError(f"Retrieval mode '{mode}' is not yet implemented.")

        now = datetime.utcnow()

        conditions = [
            AgentEpisodicMemory.agent_id == agent_id,
            AgentEpisodicMemory.valid_until.is_(None),
        ]

        if time_window_days:
            cutoff = now - timedelta(days=time_window_days)
            conditions.append(AgentEpisodicMemory.created_at >= cutoff)

        # Time-aware query expansion (detection + extraction, current mode only)
        if self._detect_temporal_query(query):
            try:
                time_range = await self._extract_time_range(query)
                if time_range:
                    logger.info(
                        "temporal_query_detected",
                        query_preview=query[:80],
                        time_range=time_range,
                    )
                    # as_of/range filtering will be applied here when those modes are implemented
            except Exception as e:
                logger.warning("temporal_query_expansion_failed", error=str(e))

        try:
            query_embedding = await embedding_service.generate_query_embedding(query)
        except Exception as e:
            logger.error("query_embedding_failed", error=str(e))
            return await self._keyword_based_retrieval(conditions, query, limit, db)

        max_candidates = max(limit * 10, 100)
        result = await db.execute(
            select(AgentEpisodicMemory)
            .where(and_(*conditions))
            .order_by(AgentEpisodicMemory.created_at.desc())
            .limit(max_candidates)
        )
        all_memories = result.scalars().all()

        min_similarity = 0.3
        scored_memories = []
        for memory in all_memories:
            if memory.embedding:
                similarity = embedding_service.cosine_similarity(
                    query_embedding, memory.embedding
                )
                if similarity >= min_similarity:
                    score = self._compute_memory_score(memory, similarity, now)
                    scored_memories.append((memory, score))

        scored_memories.sort(key=lambda x: x[1], reverse=True)
        top_memories = [m for m, _ in scored_memories[:limit]]

        logger.info(
            "memories_retrieved_by_vector_similarity",
            total_memories=len(all_memories),
            memories_with_embeddings=sum(1 for m in all_memories if m.embedding),
            above_threshold=len(scored_memories),
            returned_count=len(top_memories),
            min_similarity=min_similarity,
        )

        # Update access metadata inline (fast counter increment, same session)
        if top_memories:
            for memory in top_memories:
                memory.access_count = (memory.access_count or 0) + 1
                memory.last_accessed_at = now
            await db.flush()

        return top_memories

    async def trigger_prospective_reflection(
        self,
        agent_id: str,
        conversation_id: str,
        db: AsyncSession,
    ) -> None:
        """Generate topic-level reflection summaries after a conversation session ends.

        Groups session memories by tag and creates a reflection_summary entry per
        topic. Idempotent: skips if reflections already exist for this conversation.
        Runs as a background task — does not block the discussion-complete response.
        """
        # Idempotency check
        existing_result = await db.execute(
            select(AgentEpisodicMemory)
            .where(AgentEpisodicMemory.agent_id == agent_id)
            .where(AgentEpisodicMemory.source_conversation_id == conversation_id)
            .where(AgentEpisodicMemory.memory_type == "reflection_summary")
        )
        if existing_result.scalars().first():
            logger.info(
                "reflection_already_exists",
                agent_id=agent_id,
                conversation_id=conversation_id,
            )
            return

        session_result = await db.execute(
            select(AgentEpisodicMemory)
            .where(AgentEpisodicMemory.agent_id == agent_id)
            .where(AgentEpisodicMemory.source_conversation_id == conversation_id)
            .where(AgentEpisodicMemory.memory_type != "reflection_summary")
            .order_by(AgentEpisodicMemory.created_at.asc())
        )
        session_memories = session_result.scalars().all()

        if len(session_memories) < 3:
            logger.info(
                "insufficient_memories_for_reflection",
                agent_id=agent_id,
                count=len(session_memories),
            )
            return

        # Group by tags; untagged memories form a "session" group if there are enough
        tag_groups: Dict[str, List[AgentEpisodicMemory]] = {}
        untagged = []
        for mem in session_memories:
            mem_tags = (mem.extra_data or {}).get("tags", [])
            if mem_tags:
                for tag in mem_tags:
                    tag_groups.setdefault(tag, []).append(mem)
            else:
                untagged.append(mem)

        if len(untagged) >= 2:
            tag_groups["session"] = untagged

        now = datetime.utcnow()
        reflections_created = 0

        for topic, memories in tag_groups.items():
            if len(memories) < 2:
                continue
            try:
                memory_texts = "\n".join([f"- {m.content}" for m in memories])
                response = await llm_router.generate_response(
                    system_prompt="You summarize memory items into concise context statements.",
                    messages=[{
                        "role": "user",
                        "content": (
                            "Summarize the following related facts from a project discussion "
                            "into 1-2 sentences of useful context. "
                            "Focus on what was decided or established.\n\n"
                            f"{memory_texts}\n\nSummary:"
                        ),
                    }],
                    model="claude-haiku-4-5-20251001",
                    max_tokens=150,
                )

                summary_content = response["content"].strip()
                if not summary_content:
                    continue

                max_importance = max((m.importance or 5 for m in memories), default=5)
                dates = [m.valid_from or m.created_at for m in memories]
                time_span_days = (max(dates) - min(dates)).days

                reflection = AgentEpisodicMemory(
                    agent_id=agent_id,
                    memory_type="reflection_summary",
                    content=summary_content,
                    source_conversation_id=conversation_id,
                    confidence=1.0,
                    valid_from=now,
                    asserted_at=now,
                    importance=max_importance,
                    extra_data={
                        "tags": [topic],
                        "source_memory_ids": [m.id for m in memories],
                        "time_span_days": time_span_days,
                    },
                )
                db.add(reflection)
                reflections_created += 1

            except Exception as e:
                logger.error(
                    "reflection_generation_error",
                    agent_id=agent_id,
                    topic=topic,
                    error=str(e),
                )
                continue

        if reflections_created > 0:
            await db.flush()

        logger.info(
            "prospective_reflection_complete",
            agent_id=agent_id,
            conversation_id=conversation_id,
            reflections_created=reflections_created,
        )

    async def _keyword_based_retrieval(
        self,
        conditions: List,
        query: str,
        limit: int,
        db: AsyncSession
    ) -> List[AgentEpisodicMemory]:
        """Fallback keyword-based retrieval when embeddings are unavailable."""
        query_keywords = set(query.lower().split())

        result = await db.execute(
            select(AgentEpisodicMemory)
            .where(and_(*conditions))
            .order_by(AgentEpisodicMemory.created_at.desc())
            .limit(limit * 3)
        )
        all_memories = result.scalars().all()

        scored_memories = []
        for memory in all_memories:
            memory_keywords = set(memory.content.lower().split())
            overlap = len(query_keywords & memory_keywords)
            if overlap > 0:
                scored_memories.append((memory, overlap))

        scored_memories.sort(key=lambda x: x[1], reverse=True)

        logger.warning(
            "keyword_fallback_used",
            reason="embedding_unavailable",
            returned_count=min(len(scored_memories), limit),
        )
        return [m[0] for m in scored_memories[:limit]]

    async def update_working_memory(
        self,
        agent_id: str,
        conversation_id: str,
        goals: Optional[List[str]] = None,
        constraints: Optional[List[str]] = None,
        db: AsyncSession = None
    ):
        """Update agent's working memory."""
        result = await db.execute(
            select(AgentWorkingMemory).where(AgentWorkingMemory.agent_id == agent_id)
        )
        working_memory = result.scalar_one_or_none()

        if not working_memory:
            working_memory = AgentWorkingMemory(
                agent_id=agent_id,
                current_goals=[],
                active_constraints=[],
                conversation_contexts={},
            )
            db.add(working_memory)

        if goals is not None:
            working_memory.current_goals = goals

        if constraints is not None:
            working_memory.active_constraints = constraints

        if working_memory.conversation_contexts is None:
            working_memory.conversation_contexts = {}

        working_memory.conversation_contexts[conversation_id] = {
            "last_updated": datetime.utcnow().isoformat()
        }
        await db.flush()

    async def update_working_memory_after_response(
        self,
        agent_id: str,
        conversation_id: str,
        recent_messages: List,
        db: AsyncSession
    ):
        """Update working memory after agent responds to maintain conversation context."""
        result = await db.execute(
            select(AgentWorkingMemory).where(AgentWorkingMemory.agent_id == agent_id)
        )
        working_memory = result.scalar_one_or_none()

        if not working_memory:
            working_memory = AgentWorkingMemory(
                agent_id=agent_id,
                current_goals=[],
                active_constraints=[],
                conversation_contexts={},
            )
            db.add(working_memory)

        if working_memory.conversation_contexts is None:
            working_memory.conversation_contexts = {}

        topic = await self._extract_discussion_topic(recent_messages)

        working_memory.conversation_contexts[conversation_id] = {
            "topic": topic,
            "last_updated": datetime.utcnow().isoformat(),
            "message_count": len(recent_messages),
        }
        await db.flush()

        logger.info(
            "working_memory_updated",
            agent_id=agent_id,
            conversation_id=conversation_id,
            topic=topic,
        )

    async def _extract_discussion_topic(self, messages: List) -> str:
        """Extract concise discussion topic from recent messages."""
        if not messages:
            return "General discussion"

        conversation_text = "\n".join([
            f"{'User' if msg.sender_type == 'human' else 'Agent'}: {msg.content[:200]}"
            for msg in messages[-5:]
        ])

        try:
            response = await llm_router.generate_response(
                system_prompt="Extract a concise topic (3-8 words) from this conversation. Return ONLY the topic, nothing else.",
                messages=[{
                    "role": "user",
                    "content": f"Conversation:\n{conversation_text}\n\nTopic:",
                }],
                model=settings.default_claude_model,
                max_tokens=50,
            )
            topic = response["content"].strip().strip('"').strip("'")
            return topic[:100]
        except Exception as e:
            logger.error("topic_extraction_error", error=str(e))
            human_messages = [msg for msg in messages if msg.sender_type == "human"]
            if human_messages:
                return f"Discussion about: {human_messages[-1].content[:50]}"
            return "General discussion"

    async def clear_working_memory_conversation(
        self,
        agent_id: str,
        conversation_id: str,
        db: AsyncSession
    ):
        """Clear working memory context for a specific conversation."""
        result = await db.execute(
            select(AgentWorkingMemory).where(AgentWorkingMemory.agent_id == agent_id)
        )
        working_memory = result.scalar_one_or_none()

        if not working_memory:
            return

        if (
            working_memory.conversation_contexts
            and conversation_id in working_memory.conversation_contexts
        ):
            del working_memory.conversation_contexts[conversation_id]
            await db.flush()
            logger.info(
                "working_memory_conversation_cleared",
                agent_id=agent_id,
                conversation_id=conversation_id,
            )


# Singleton instance
memory_service = MemoryService()
