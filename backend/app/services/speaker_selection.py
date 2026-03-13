"""Speaker selection service for multi-agent orchestration.

DEPRECATED: This module is superseded by bid_service.py and facilitation.py
(bid-based agent orchestration, Phase 5+). The import in chat.py is kept for
import-compatibility during transition; delete once confirmed stable.
"""
from typing import List, Dict, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import structlog
from datetime import datetime

from app.models import Agent, Message, ConversationParticipant
from app.services.websocket_manager import ws_manager
from app.services.embedding_service import embedding_service

logger = structlog.get_logger()


class SpeakerSelectionService:
    """Service for selecting which agents should speak based on relevance."""

    async def select_speakers(
        self,
        conversation_id: str,
        message_content: str,
        participant_agent_ids: List[str],
        db: AsyncSession,
        max_speakers: int = 3,
        min_relevance_score: float = 0.6,
        responding_to_agent: bool = False,
        last_speaker_id: str = None,
        last_n_speakers: List[str] = None,
    ) -> List[Tuple[str, float]]:
        """
        Select which agents should respond based on message relevance.

        Args:
            conversation_id: ID of the conversation
            message_content: Content of the message to respond to
            participant_agent_ids: List of agent IDs in the conversation
            db: Database session
            max_speakers: Maximum number of agents to select
            min_relevance_score: Minimum relevance score (0-1) for selection
            responding_to_agent: True if responding to another agent (adds engagement bonus)
            last_speaker_id: ID of the agent who spoke last (for complementary expertise, DEPRECATED - use last_n_speakers)
            last_n_speakers: List of agent IDs who spoke recently (will be excluded to prevent consecutive responses)

        Returns:
            List of tuples (agent_id, relevance_score) sorted by relevance
        """
        if not participant_agent_ids:
            return []

        logger.info(
            "selecting_speakers",
            conversation_id=conversation_id,
            participant_count=len(participant_agent_ids)
        )

        # Get all participant agents
        result = await db.execute(
            select(Agent).where(Agent.id.in_(participant_agent_ids))
        )
        agents = result.scalars().all()

        # Filter out agents who just spoke (hard exclusion to prevent consecutive responses)
        if last_n_speakers:
            excluded_count = len([a for a in agents if a.id in last_n_speakers])
            agents = [a for a in agents if a.id not in last_n_speakers]
            if excluded_count > 0:
                logger.info(
                    "excluding_recent_speakers",
                    conversation_id=conversation_id,
                    excluded_count=excluded_count,
                    excluded_ids=last_n_speakers
                )

        # Backward compatibility: also exclude last_speaker_id if provided (and not already in last_n_speakers)
        if last_speaker_id and (not last_n_speakers or last_speaker_id not in last_n_speakers):
            agents = [a for a in agents if a.id != last_speaker_id]
            logger.info(
                "excluding_last_speaker",
                conversation_id=conversation_id,
                last_speaker_id=last_speaker_id
            )

        # Get last speaker's info for complementary expertise bonus
        last_speaker = None
        if last_speaker_id:
            # Need to re-fetch from original list since we may have filtered it out
            result_all = await db.execute(
                select(Agent).where(Agent.id == last_speaker_id)
            )
            last_speaker = result_all.scalar_one_or_none()

        # Get list of agents who have spoken in this conversation (for fresh voice bonus)
        agents_who_spoke = await self._get_agents_who_spoke(conversation_id, db)

        # Score each agent's relevance
        agent_scores = []
        all_agent_details = []  # Track all agents for debugging

        for agent in agents:
            has_spoken_before = agent.id in agents_who_spoke
            score, score_breakdown = await self._score_agent_relevance(
                agent, message_content, conversation_id, db, responding_to_agent, last_speaker, has_spoken_before
            )

            agent_detail = {
                "agent_id": agent.id,
                "agent_name": agent.name,
                "total_score": round(score, 3),
                "meets_threshold": score >= min_relevance_score,
                "threshold": min_relevance_score,
                **score_breakdown
            }
            all_agent_details.append(agent_detail)

            # Log individual agent scoring
            log_data = {
                "agent_name": agent.name,
                "total_score": round(score, 3),
                "matching_method": score_breakdown["matching_method"],
                "recency_score": round(score_breakdown["recency_score"], 3),
                "meets_threshold": score >= min_relevance_score,
                "threshold": min_relevance_score
            }

            # Add method-specific scores
            if score_breakdown["matching_method"] == "semantic":
                log_data["semantic_score"] = score_breakdown["semantic_score"]
            else:
                log_data["keyword_score"] = score_breakdown["keyword_score"]
                log_data["criteria_score"] = score_breakdown["criteria_score"]

            logger.info("agent_scored", **log_data)

            if score >= min_relevance_score:
                agent_scores.append((agent.id, score, agent.name))

        # Sort by score (descending) and limit
        agent_scores.sort(key=lambda x: x[1], reverse=True)
        selected = agent_scores[:max_speakers]

        # Log selection summary
        selected_agents = [(name, round(score, 3)) for _, score, name in selected]
        rejected_agents = [
            (d["agent_name"], d["total_score"])
            for d in all_agent_details
            if not d["meets_threshold"]
        ]

        logger.info(
            "speakers_selected",
            conversation_id=conversation_id,
            total_agents_scored=len(agents),
            selected_count=len(selected),
            rejected_count=len(rejected_agents),
            selected_agents=selected_agents,
            rejected_agents=rejected_agents if rejected_agents else "none",
            min_threshold=min_relevance_score,
            message_preview=message_content[:100]
        )

        # Send debug event via WebSocket
        try:
            await ws_manager.send_debug_event(
                conversation_id=conversation_id,
                event_type="agent_selection",
                data={
                    "timestamp": datetime.utcnow().isoformat(),
                    "message_preview": message_content[:100],
                    "total_agents": len(agents),
                    "threshold": min_relevance_score,
                    "agents_scored": all_agent_details,
                    "selected_count": len(selected),
                    "rejected_count": len(rejected_agents)
                }
            )
        except Exception as e:
            logger.error("debug_event_send_error", error=str(e))

        # Return in original format (agent_id, score)
        return [(aid, score) for aid, score, _ in selected]

    async def _score_agent_relevance(
        self,
        agent: Agent,
        message_content: str,
        conversation_id: str,
        db: AsyncSession,
        responding_to_agent: bool = False,
        last_speaker: Agent = None,
        has_spoken_before: bool = True,
    ) -> Tuple[float, Dict]:
        """
        Score how relevant an agent is to respond to a message.

        Uses a combination of:
        - Semantic similarity (embedding-based matching) OR keyword fallback
        - Recent activity (agents who spoke recently get lower scores)
        - Agent-to-agent bonus (when responding to another agent)
        - Complementary expertise bonus (different perspective from last speaker)

        Args:
            agent: The agent to score
            message_content: The message content
            conversation_id: Conversation ID
            db: Database session
            responding_to_agent: True if responding to another agent (adds engagement bonus)
            last_speaker: The agent who spoke last (for complementary expertise)

        Returns:
            Tuple of (relevance score between 0 and 1, breakdown dict)
        """
        score = 0.0

        # Agent-to-agent engagement bonus (applied before other scoring)
        agent_to_agent_bonus = 0.0
        if responding_to_agent:
            agent_to_agent_bonus = 0.15  # 15% bonus for responding to other agents
            score += agent_to_agent_bonus

        # Complementary expertise bonus (different perspective)
        complementary_bonus = 0.0
        if last_speaker and last_speaker.id != agent.id:
            # Check if this agent's expertise is different from last speaker's
            last_expertise = set(last_speaker.expertise_domain.lower().split())
            current_expertise = set(agent.expertise_domain.lower().split())

            # If there's less than 30% overlap, they have complementary expertise
            if last_expertise and current_expertise:
                overlap = len(last_expertise & current_expertise) / len(last_expertise | current_expertise)
                if overlap < 0.3:
                    complementary_bonus = 0.10  # 10% bonus for different perspective
                    score += complementary_bonus

        # Fresh voice bonus (agent hasn't spoken in this conversation yet)
        fresh_voice_bonus = 0.0
        if not has_spoken_before:
            fresh_voice_bonus = 0.15  # 15% bonus for bringing in new perspective
            score += fresh_voice_bonus

        # SEMANTIC MATCHING (70% weight) or keyword fallback
        use_semantic = bool(agent.embedding)
        semantic_score = 0.0
        keyword_score = 0.0
        criteria_score = 0.0

        if use_semantic:
            # Use embedding-based semantic matching
            try:
                message_embedding = await embedding_service.generate_embedding(message_content)
                semantic_similarity = embedding_service.cosine_similarity(
                    message_embedding,
                    agent.embedding
                )
                # Convert from [-1, 1] to [0, 1] range
                semantic_score = (semantic_similarity + 1) / 2
                weighted_semantic = semantic_score * 0.7
                score += weighted_semantic

                logger.debug(
                    "semantic_matching_used",
                    agent_name=agent.name,
                    semantic_score=round(semantic_score, 3),
                    weighted=round(weighted_semantic, 3)
                )
            except Exception as e:
                logger.error("semantic_matching_failed", agent_name=agent.name, error=str(e))
                # Fall back to keyword matching
                use_semantic = False

        if not use_semantic:
            # Fall back to keyword + criteria matching (legacy)
            keyword_score = self._keyword_match_score(
                message_content.lower(),
                agent.expertise_domain.lower()
            )
            weighted_keyword = keyword_score * 0.4
            score += weighted_keyword

            if agent.participation_criteria:
                criteria_score = self._criteria_match_score(
                    message_content.lower(),
                    agent.participation_criteria
                )
                weighted_criteria = criteria_score * 0.3
                score += weighted_criteria

        # RECENCY PENALTY (adaptive weight based on domain expertise)
        # Calculate domain expertise strength based on matching method used
        if use_semantic:
            domain_expertise_strength = semantic_score
        else:
            domain_expertise_strength = (keyword_score * 0.6) + (criteria_score * 0.4)

        # Adaptive recency weight:
        # - High expertise (0.7+): 5% weight (minimal penalty for experts)
        # - Medium expertise (0.4-0.7): 15% weight (moderate penalty)
        # - Low expertise (<0.4): 25% weight (full penalty for off-topic agents)
        # Note: Lowered thresholds and weights for semantic matching responsiveness
        if domain_expertise_strength >= 0.7:
            recency_weight = 0.05  # Domain expert - minimal penalty
        elif domain_expertise_strength >= 0.4:
            recency_weight = 0.15  # Moderately relevant - moderate penalty
        else:
            recency_weight = 0.25  # Off-topic - full penalty

        recency_score = await self._recency_score(agent.id, conversation_id, db)
        weighted_recency = recency_score * recency_weight
        score += weighted_recency

        final_score = min(score, 1.0)

        breakdown = {
            "matching_method": "semantic" if use_semantic else "keyword",
            "semantic_score": round(semantic_score, 3) if use_semantic else None,
            "keyword_score": round(keyword_score, 3) if not use_semantic else None,
            "criteria_score": round(criteria_score, 3) if not use_semantic else None,
            "recency_score": recency_score,
            "recency_weight": round(recency_weight, 3),
            "weighted_recency": round(weighted_recency, 3),
            "domain_expertise_strength": round(domain_expertise_strength, 3),
            "agent_to_agent_bonus": round(agent_to_agent_bonus, 3),
            "complementary_bonus": round(complementary_bonus, 3),
            "fresh_voice_bonus": round(fresh_voice_bonus, 3),
            "has_spoken_before": has_spoken_before,
            "responding_to_agent": responding_to_agent,
            "expertise_domain": agent.expertise_domain,
            "has_embedding": bool(agent.embedding)
        }

        return final_score, breakdown

    def _keyword_match_score(self, message: str, expertise: str) -> float:
        """
        Score based on keyword overlap.

        Args:
            message: Message content (lowercase)
            expertise: Agent expertise domain (lowercase)

        Returns:
            Score between 0 and 1
        """
        # Extract keywords from expertise (split by common delimiters)
        expertise_keywords = set(
            expertise.replace(',', ' ').replace(';', ' ').split()
        )

        # Count matches
        matches = sum(1 for keyword in expertise_keywords if keyword in message)

        if not expertise_keywords:
            return 0.0  # No keywords defined, no keyword relevance

        # Score based on percentage of keywords matched
        match_score = matches / len(expertise_keywords) if expertise_keywords else 0
        return min(match_score, 1.0)

    def _criteria_match_score(
        self, message: str, criteria: Dict
    ) -> float:
        """
        Score based on participation criteria.

        Args:
            message: Message content (lowercase)
            criteria: Agent's participation criteria dict

        Returns:
            Score between 0 and 1
        """
        if not criteria:
            return 0.0

        # Check for keywords in criteria
        if "keywords" in criteria:
            keywords = criteria["keywords"]
            if isinstance(keywords, list):
                matches = sum(1 for kw in keywords if kw.lower() in message)
                if keywords:
                    return min(matches / len(keywords), 1.0)

        # Criteria exist but don't match well
        return 0.1

    async def _get_agents_who_spoke(
        self, conversation_id: str, db: AsyncSession
    ) -> set:
        """
        Get set of agent IDs who have spoken in this conversation.

        Args:
            conversation_id: Conversation ID
            db: Database session

        Returns:
            Set of agent IDs who have participated
        """
        result = await db.execute(
            select(Message.sender_id)
            .where(Message.conversation_id == conversation_id)
            .where(Message.sender_type == "agent")
            .distinct()
        )
        return {row[0] for row in result.all() if row[0]}

    async def _recency_score(
        self, agent_id: str, conversation_id: str, db: AsyncSession
    ) -> float:
        """
        Score based on recency of agent's last message.

        Agents who just spoke get lower scores to encourage diversity.

        Args:
            agent_id: Agent ID
            conversation_id: Conversation ID
            db: Database session

        Returns:
            Score between 0 and 1 (1 = hasn't spoken recently, 0 = just spoke)
        """
        # Get last N messages
        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(5)
        )
        recent_messages = result.scalars().all()

        if not recent_messages:
            return 1.0  # No messages yet, full score

        # Check how recently this agent spoke
        for i, msg in enumerate(recent_messages):
            if msg.sender_type == "agent" and msg.sender_id == agent_id:
                # Penalize based on recency:
                # Just spoke (i=0): 0.0
                # Spoke 1 message ago: 0.25
                # Spoke 2 messages ago: 0.5
                # Spoke 3+ messages ago: 0.75+
                return min(i * 0.3, 1.0)

        # Didn't speak in last 5 messages
        return 1.0


# Singleton instance
speaker_selection_service = SpeakerSelectionService()
