"""Agent runtime service for generating responses."""
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import structlog
import asyncio
import time
from datetime import datetime

from app.models import Agent, Message, AgentWorkingMemory, AgentSemanticMemory, AgentEpisodicMemory
from app.services.llm_router import llm_router
from app.services.memory_service import memory_service
from app.services.websocket_manager import ws_manager
from app.services import whiteboard_service
from app.config import settings
from datetime import datetime as _datetime_cls

logger = structlog.get_logger()


class AgentService:
    """Service for agent response generation."""

    async def generate_agent_bid(
        self,
        agent_id: str,
        conversation_id: str,
        db: AsyncSession,
        participant_agents: list = None,
        context_messages: list = None,
        pending_questions: list = None,
        available_agents: list = None,
    ) -> "BidResult":
        """
        Generate a lightweight bid from an agent before the full response.

        Uses claude-haiku-4-5-20251001 for low latency — bids must be fast.
        Max 8 context messages (no full memory retrieval).

        Returns:
            BidResult with turn_type, confidence, target, and preview.
            On any failure, returns BidResult(turn_type='pass', confidence=0.0).
        """
        from app.services.bid_service import BidResult, TURN_TYPE_PASS
        import anthropic

        try:
            agent_result = await db.execute(
                select(Agent).where(Agent.id == agent_id)
            )
            agent = agent_result.scalar_one_or_none()
            if not agent:
                return BidResult(agent_id=agent_id, turn_type=TURN_TYPE_PASS, confidence=0.0)

            # Build lightweight context (max 8 messages)
            agent_name_map = {a.id: a.name for a in (participant_agents or [])}
            messages_for_bid = (context_messages or [])[-8:]

            bid_messages = []
            for msg in messages_for_bid:
                if msg.sender_type == "human":
                    bid_messages.append({"role": "user", "content": f"[Human]: {msg.content}"})
                elif msg.sender_type == "agent":
                    if msg.sender_id == agent_id:
                        bid_messages.append({"role": "assistant", "content": msg.content})
                    else:
                        other_name = agent_name_map.get(msg.sender_id, "Agent")
                        bid_messages.append({"role": "user", "content": f"[{other_name}]: {msg.content}"})
                elif msg.sender_type == "system":
                    bid_messages.append({"role": "user", "content": f"[System]: {msg.content}"})

            bid_messages = self._merge_consecutive_messages(bid_messages)

            if bid_messages and bid_messages[0]["role"] == "assistant":
                bid_messages.insert(0, {"role": "user", "content": "[Conversation in progress]"})

            # Detect if the last message in context is this agent's own — they just spoke.
            just_spoke = bid_messages and bid_messages[-1]["role"] == "assistant"

            if not bid_messages:
                bid_messages = [{"role": "user", "content": "What would you contribute to this discussion?"}]

            other_agents = [a for a in (participant_agents or []) if a.id != agent_id]
            other_names = ", ".join(a.name for a in other_agents) if other_agents else "no other agents"

            # Build target options: other agents + the human participant
            agent_target_options = "; ".join(
                f'{a.id} ({a.name})' for a in (participant_agents or []) if a.id != agent_id
            )
            target_options = '"human" (the conversation\'s human participant)'
            if agent_target_options:
                target_options = target_options + "; " + agent_target_options

            # Build available agents context for proposal bids
            available_agent_options = ""
            if available_agents:
                non_participants = [a for a in available_agents if a.id not in {pa.id for pa in (participant_agents or [])}]
                if non_participants:
                    available_agent_options = "\n\nAgents available to add: " + "; ".join(
                        f"{a.id} ({a.name}, expertise: {a.expertise_domain})" for a in non_participants
                    )

            bid_system = (
                f"You are {agent.name}, an AI agent with expertise in {agent.expertise_domain}.\n\n"
                f"Other participants in this conversation: {other_names}.\n\n"
                "Before deciding to bid, read what the prior agents said carefully.\n"
                "Ask yourself: is the CORE INSIGHT of my contribution already in the conversation?\n"
                "This includes your OWN prior turns — do not restate or rephrase something you\n"
                "already said, even if the other agents haven't responded to it yet.\n"
                "If yes — bid 'pass', even if your framing or domain angle differs.\n"
                "Rephrasing the same conclusion from your area of expertise is not a new contribution.\n"
                "Only bid to speak if you have something SUBSTANTIVELY different: a contradiction,\n"
                "a genuinely new piece of information, a synthesis no one has made yet, or a\n"
                "specific follow-up question to another agent.\n"
                "\n"
                "Check for circular debate: If the same positions have been stated and challenged\n"
                "multiple times without either side introducing new evidence, bid 'convergence'.\n"
                "Restating your argument more thoroughly is not progress — it's noise.\n"
                "If you have already made this point and another agent has already challenged it,\n"
                "bid 'convergence' or 'pass' instead of challenging again.\n"
                "\n"
                "Your primary audience is the other agents — address them directly.\n"
                "\n"
                "If you just asked a question in your most recent turn, bid 'pass' now.\n"
                "Wait for the other agents to answer before speaking again.\n"
                "\n"
                "Bid 'question' with target=<agent_id> to direct a question to a specific agent.\n"
                "Bid 'question' with target='human' ONLY to ask the actual human user — this ends\n"
                "the discussion and returns control to them. Do NOT use target='human' to signal\n"
                "that you want other AI agents to respond first.\n"
                "\n"
                "Bid 'propose_addition' if YOU want to add an agent — this executes your proposal immediately. "
                "Do not wait for the group to agree first, and do not assume someone else will do it. "
                "If you think an agent should be added, YOU bid it. "
                "Do not discuss the idea in a conveyance or challenge turn and then bid convergence — "
                "bid 'propose_addition' directly. Leave target EMPTY. Describe the needed expertise in 'preview'.\n"
                "Bid 'propose_removal' to suggest removing an agent that is no longer needed. "
                "Set target=agent_id from the current participants list. Use 'preview' to explain why.\n"
                "Only use proposals when the benefit is clear and significant — not on a whim."
                + available_agent_options
            )

            # If a membership proposal just resolved, suppress immediate re-discussion.
            # Agents already expressed their position during the debate phase; rehashing
            # it in the next turn is pure noise.
            recent_proposal_resolved = any(
                getattr(m, "sender_type", None) == "system"
                and isinstance(getattr(m, "extra_data", None), dict)
                and m.extra_data.get("proposal_phase") == "result"
                for m in (context_messages or [])[-5:]
            )
            if recent_proposal_resolved:
                bid_system += (
                    "\n\nA membership proposal was just resolved (see the system message above). "
                    "The proposal topic is now CLOSED — do not discuss it or restate your position on it. "
                    "If the main conversation topic still needs work, address that. "
                    "If there is nothing new to add to the main topic, bid 'pass'."
                )

            # If the agent just spoke, append a strong instruction.
            # We still make the API call (instead of short-circuiting) so the agent
            # can bid propose_addition/propose_removal right after describing a proposal.
            if just_spoke:
                bid_system += (
                    "\n\nYou JUST SPOKE — your message is the most recent one. "
                    "You must bid 'pass' UNLESS you want to bid 'propose_addition' or 'propose_removal'. "
                    "No other bid type is allowed immediately after speaking."
                )

            # Append pending questions context if any exist
            if pending_questions:
                pending_q_str = "\n".join(f"- {q[:200]}" for q in pending_questions)
                bid_system += (
                    f"\n\nPending questions already asked to the human (not yet answered):\n"
                    f"{pending_q_str}\n"
                    "Do NOT bid 'question' with target='human' for the same topic — the human "
                    "has already been asked. If these questions are blocking you, state your "
                    "assumption explicitly and proceed, or bid 'pass'."
                )

            bid_tool = {
                "name": "submit_bid",
                "description": "Submit your participation bid for the next turn",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "turn_type": {
                            "type": "string",
                            "enum": ["conveyance", "challenge", "question", "convergence", "pass", "backchannel", "propose_addition", "propose_removal"],
                            "description": (
                                "conveyance: genuinely new information not yet in the conversation; "
                                "challenge: you have a specific contradiction or substantially different view — you can point to evidence; "
                                "question: directed inquiry to another agent; "
                                "convergence: the key points have been made — synthesize what's been agreed and what remains open; "
                                "use convergence when further debate won't produce new insights; "
                                "pass: nothing genuinely new to add; "
                                "backchannel: brief acknowledgment only, not claiming the floor; "
                                "propose_addition: if YOU want to add an agent, bid this — it executes your proposal immediately; do not wait for group agreement or assume someone else will do it; leave target EMPTY, describe needed expertise in 'preview'; "
                                "propose_removal: EXECUTE a proposal to remove an agent — set target=agent_id; "
                            ),
                        },
                        "confidence": {
                            "type": "number",
                            "description": "How important is your contribution? (0.0 to 1.0). Use 0.0 for pass.",
                        },
                        "target": {
                            "type": "string",
                            "description": (
                                f"For 'question': who to direct the question to. "
                                f"Options: {target_options}. "
                                "IMPORTANT: 'human' means the actual human person using this application — "
                                "not another AI agent. Use 'human' only when you need the human's "
                                "input or decision; this ends the agent discussion. "
                                "To ask another AI agent, use their agent ID — not 'human'. "
                                "For 'propose_removal': set target=agent_id of the agent to remove. "
                                "For 'propose_addition': leave target EMPTY (gap description goes in 'preview'). "
                                "Leave empty for all other turn types."
                            ),
                        },
                        "preview": {
                            "type": "string",
                            "description": (
                                "One sentence stating who/what you are responding to and what you'll contribute. "
                                "Example: 'Responding to [AgentName]'s point on X — I'll challenge their assumption about Y.' "
                                "For 'propose_addition': describe the expertise gap, e.g. 'We need a legal expert to assess regulatory risk.' "
                                "Leave empty for pass."
                            ),
                        },
                    },
                    "required": ["turn_type", "confidence"],
                },
            }

            client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    system=bid_system,
                    messages=bid_messages,
                    max_tokens=256,
                    temperature=0.7,
                    tools=[bid_tool],
                    tool_choice={"type": "tool", "name": "submit_bid"},
                ),
            )

            bid_data: dict = {}
            for block in response.content:
                if block.type == "tool_use" and block.name == "submit_bid":
                    bid_data = block.input
                    break

            valid_types = {"conveyance", "challenge", "question", "convergence", "pass", "backchannel", "propose_addition", "propose_removal"}
            turn_type = bid_data.get("turn_type", TURN_TYPE_PASS)
            if turn_type not in valid_types:
                turn_type = TURN_TYPE_PASS

            confidence = float(bid_data.get("confidence", 0.0))
            target = bid_data.get("target") or None
            preview = bid_data.get("preview") or None

            # Code-level enforcement: if the agent just spoke, only proposal bids are allowed.
            # The LLM sometimes ignores the instruction to pass after speaking.
            if just_spoke and turn_type not in {"propose_addition", "propose_removal", "pass", "backchannel"}:
                logger.debug(
                    "agent_bid_forced_pass_just_spoke",
                    agent_name=agent.name,
                    original_turn_type=turn_type,
                )
                turn_type = TURN_TYPE_PASS
                confidence = 0.0

            logger.debug(
                "agent_bid_generated",
                agent_name=agent.name,
                turn_type=turn_type,
                confidence=round(confidence, 2),
                has_target=target is not None,
                has_preview=preview is not None,
            )

            return BidResult(
                agent_id=agent_id,
                turn_type=turn_type,
                confidence=confidence,
                target=target,
                preview=preview,
            )

        except Exception as e:
            logger.error(
                "agent_bid_error",
                agent_id=agent_id,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return BidResult(agent_id=agent_id, turn_type=TURN_TYPE_PASS, confidence=0.0)

    async def generate_agent_response(
        self,
        agent_id: str,
        conversation_id: str,
        db: AsyncSession,
        max_context_messages: int = 20,
        participant_agents: list = None,
        bid_result: "Optional[BidResult]" = None,
        pending_questions: list = None,
    ) -> Dict[str, Any]:
        """
        Generate a response from an agent based on conversation context.

        Args:
            agent_id: ID of the agent
            conversation_id: ID of the conversation
            db: Database session
            max_context_messages: Number of recent messages to include as context
            participant_agents: Other agents in the conversation
            bid_result: Optional BidResult from the agent's prior bid (for prompt context)

        Returns:
            Dict with 'content' (response text) and 'discussion_complete' (boolean, legacy)
        """
        # Get agent
        agent_result = await db.execute(
            select(Agent).where(Agent.id == agent_id)
        )
        agent = agent_result.scalar_one_or_none()
        if not agent:
            raise ValueError(f"Agent {agent_id} not found")

        # Load working memory for conversation context
        working_memory_result = await db.execute(
            select(AgentWorkingMemory).where(AgentWorkingMemory.agent_id == agent_id)
        )
        working_memory = working_memory_result.scalar_one_or_none()

        start_time = time.time()

        logger.info(
            "generating_agent_response_start",
            agent_id=agent_id,
            agent_name=agent.name,
            conversation_id=conversation_id,
            max_context_messages=max_context_messages,
            model=agent.model
        )

        # Get recent messages for context
        messages_result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(max_context_messages)
        )
        recent_messages = list(reversed(messages_result.scalars().all()))

        logger.info(
            "context_messages_loaded",
            agent_name=agent.name,
            message_count=len(recent_messages),
            latest_message_preview=recent_messages[-1].content[:100] if recent_messages else "none"
        )

        # Build agent name lookup from participants
        agent_name_map = {}
        if participant_agents:
            for a in participant_agents:
                agent_name_map[a.id] = a.name

        # Build conversation history for Claude
        # - Human messages → role: "user" with [Human] prefix (label survives consecutive-message merging)
        # - Current agent's messages → role: "assistant" (its own prior output)
        # - Other agents' messages → role: "user" with [AgentName] prefix
        claude_messages = []
        for msg in recent_messages:
            if msg.sender_type == "human":
                role = "user"
                content = f"[Human]: {msg.content}"
            elif msg.sender_type == "agent":
                if msg.sender_id == agent_id:
                    role = "assistant"
                    content = msg.content
                else:
                    role = "user"
                    other_name = agent_name_map.get(msg.sender_id, "Agent")
                    content = f"[{other_name}]: {msg.content}"
            elif msg.sender_type == "system":
                role = "user"
                content = f"[System]: {msg.content}"
            else:
                continue

            claude_messages.append({
                "role": role,
                "content": content
            })

        # Merge consecutive same-role messages (required by Claude/OpenAI APIs)
        claude_messages = self._merge_consecutive_messages(claude_messages)

        # Guard: if first message is "assistant", prepend a user message
        if claude_messages and claude_messages[0]["role"] == "assistant":
            claude_messages.insert(0, {"role": "user", "content": "[Conversation in progress]"})

        # Retrieve relevant memories
        last_message = recent_messages[-1] if recent_messages else None
        memories = []
        if last_message:
            logger.info(
                "retrieving_memories",
                agent_name=agent.name,
                query_preview=last_message.content[:100]
            )
            memories = await memory_service.retrieve_relevant_memories(
                agent_id=agent_id,
                query=last_message.content,
                db=db,
                conversation_id=conversation_id,
                limit=settings.max_episodic_retrieval
            )
            memory_details = [{
                "content_preview": m.content[:80],
                "memory_type": m.memory_type,
                "confidence": m.confidence,
                "valid_from": m.valid_from.isoformat() if m.valid_from else None,
                "importance": m.importance,
            } for m in memories]

            logger.info(
                "memories_retrieved",
                agent_name=agent.name,
                memory_count=len(memories),
                memories=memory_details
            )

            # Send debug event via WebSocket
            try:
                await ws_manager.send_debug_event(
                    conversation_id=conversation_id,
                    event_type="memory_retrieval",
                    data={
                        "timestamp": datetime.utcnow().isoformat(),
                        "agent_name": agent.name,
                        "agent_id": agent_id,
                        "query_preview": last_message.content[:100],
                        "memory_count": len(memories),
                        "memories": memory_details
                    }
                )
            except Exception as e:
                logger.error("debug_event_send_error", error=str(e))
        else:
            logger.info(
                "no_context_for_memory_retrieval",
                agent_name=agent.name
            )

        # Load semantic memories (always load all - it's a small set of consolidated knowledge)
        semantic_result = await db.execute(
            select(AgentSemanticMemory)
            .where(AgentSemanticMemory.agent_id == agent_id)
            .order_by(AgentSemanticMemory.category, AgentSemanticMemory.created_at.desc())
        )
        semantic_memories = semantic_result.scalars().all()

        logger.info(
            "semantic_memories_loaded",
            agent_name=agent.name,
            semantic_count=len(semantic_memories)
        )

        # Fetch current whiteboard state
        whiteboard_entries = await whiteboard_service.get_current_state(conversation_id, db)

        # Build system prompt with memories, participant context, and working memory
        system_prompt = self._build_system_prompt(
            agent,
            memories,
            participant_agents,
            working_memory=working_memory,
            conversation_id=conversation_id,
            semantic_memories=semantic_memories,
            bid_result=bid_result,
            pending_questions=pending_questions,
            whiteboard_entries=whiteboard_entries,
        )

        logger.info(
            "system_prompt_built",
            agent_name=agent.name,
            prompt_length=len(system_prompt),
            has_memories=len(memories) > 0,
            has_participant_context=participant_agents is not None and len(participant_agents) > 1
        )

        # Generate response using agent's preferred model (or default)
        logger.info(
            "calling_claude_api",
            agent_name=agent.name,
            model=agent.model,
            message_count=len(claude_messages),
            system_prompt_length=len(system_prompt)
        )

        # Pre-response whiteboard_history tool loop (Claude models only)
        # Allows agents to look up change history before crafting their response
        is_claude_model = (
            agent.model is None
            or agent.model.startswith("claude-")
        )
        if is_claude_model and whiteboard_entries:
            claude_messages = await self._run_whiteboard_history_loop(
                agent=agent,
                system_prompt=system_prompt,
                claude_messages=claude_messages,
                conversation_id=conversation_id,
                db=db,
            )

        api_start_time = time.time()
        try:
            response = await llm_router.generate_response(
                system_prompt=system_prompt,
                messages=claude_messages,
                model=agent.model,  # Use agent's model if specified
            )
            api_duration = time.time() - api_start_time

            # Check for empty or whitespace-only responses
            content_stripped = response["content"].strip()
            is_empty = len(content_stripped) == 0

            if is_empty:
                logger.warning(
                    "agent_empty_response_detected",
                    agent_name=agent.name,
                    output_tokens=response["usage"]["output_tokens"],
                    raw_content=repr(response["content"]),  # Show raw content with quotes/whitespace
                    stop_reason=response.get("stop_reason")
                )

            logger.info(
                "agent_response_generated",
                agent_name=agent.name,
                input_tokens=response["usage"]["input_tokens"],
                output_tokens=response["usage"]["output_tokens"],
                total_tokens=response["usage"]["input_tokens"] + response["usage"]["output_tokens"],
                memories_used=len(memories),
                api_duration_seconds=round(api_duration, 2),
                response_preview=response["content"][:150],
                is_empty=is_empty
            )

            # Send debug event via WebSocket
            try:
                await ws_manager.send_debug_event(
                    conversation_id=conversation_id,
                    event_type="response_generated",
                    data={
                        "timestamp": datetime.utcnow().isoformat(),
                        "agent_name": agent.name,
                        "agent_id": agent_id,
                        "model": agent.model,
                        "input_tokens": response["usage"]["input_tokens"],
                        "output_tokens": response["usage"]["output_tokens"],
                        "total_tokens": response["usage"]["input_tokens"] + response["usage"]["output_tokens"],
                        "memories_used": len(memories),
                        "api_duration_seconds": round(api_duration, 2),
                        "response_preview": response["content"][:150],
                        "raw_content": repr(response["content"][:200]),  # Include raw content for debugging
                        "is_empty": is_empty,
                        "stop_reason": response.get("stop_reason"),
                        "propose_agent": response.get("propose_agent", ""),
                        "success": True
                    }
                )
            except Exception as e:
                logger.error("debug_event_send_error", error=str(e))
        except Exception as e:
            api_duration = time.time() - api_start_time
            logger.error(
                "claude_api_error",
                agent_name=agent.name,
                error_type=type(e).__name__,
                error_message=str(e),
                api_duration_seconds=round(api_duration, 2)
            )

            # Send error debug event via WebSocket
            try:
                await ws_manager.send_debug_event(
                    conversation_id=conversation_id,
                    event_type="api_error",
                    data={
                        "timestamp": datetime.utcnow().isoformat(),
                        "agent_name": agent.name,
                        "agent_id": agent_id,
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                        "api_duration_seconds": round(api_duration, 2),
                        "success": False
                    }
                )
            except Exception as ws_error:
                logger.error("debug_event_send_error", error=str(ws_error))

            raise

        # Update working memory with conversation state
        try:
            await memory_service.update_working_memory_after_response(
                agent_id=agent_id,
                conversation_id=conversation_id,
                recent_messages=recent_messages,
                db=db
            )
        except Exception as e:
            logger.error(
                "working_memory_update_error",
                agent_name=agent.name,
                error=str(e)
            )

        # Extract and store new memories only when triggered by a human message
        # (agent-to-agent turns don't introduce new information worth memorizing)
        if last_message and last_message.sender_type == "human":
            try:
                logger.info(
                    "extracting_new_memories",
                    agent_name=agent.name,
                    conversation_id=conversation_id
                )
                await memory_service.extract_memories_from_conversation(
                    agent_id=agent_id,
                    conversation_id=conversation_id,
                    recent_message_count=10,
                    db=db
                )
                logger.info(
                    "memory_extraction_complete",
                    agent_name=agent.name
                )
            except Exception as e:
                logger.error(
                    "memory_extraction_error",
                    agent_name=agent.name,
                    error_type=type(e).__name__,
                    error_message=str(e)
                )

        # Process whiteboard updates from the agent's response
        whiteboard_updates = response.get("whiteboard_updates", []) or []
        if whiteboard_updates:
            for update in whiteboard_updates:
                action = update.get("action")
                key = update.get("key", "").strip()
                reason = update.get("reason", "")
                if not key:
                    continue
                if action == "set":
                    try:
                        await whiteboard_service.set_entry(
                            conversation_id=conversation_id,
                            key=key,
                            entry_type=update.get("entry_type", "strategy"),
                            value=update.get("value", ""),
                            reason=reason,
                            author_id=agent_id,
                            author_type="agent",
                            author_name=agent.name,
                            db=db,
                        )
                    except ValueError:
                        logger.warning(
                            "whiteboard_update_too_long",
                            agent_name=agent.name,
                            key=key,
                        )
                elif action == "remove":
                    await whiteboard_service.remove_entry(
                        conversation_id=conversation_id,
                        key=key,
                        reason=reason,
                        author_id=agent_id,
                        author_type="agent",
                        author_name=agent.name,
                        db=db,
                    )

            # Broadcast whiteboard changes
            try:
                updated_entries = await whiteboard_service.get_current_state(conversation_id, db)
                await self._broadcast_whiteboard_change(
                    conversation_id=conversation_id,
                    entries=updated_entries,
                    updates=whiteboard_updates,
                    agent_name=agent.name,
                    db=db,
                )
            except Exception as e:
                logger.error("whiteboard_broadcast_error", error=str(e))

        total_duration = time.time() - start_time
        logger.info(
            "generate_agent_response_complete",
            agent_name=agent.name,
            total_duration_seconds=round(total_duration, 2),
            success=True
        )

        return {
            "content": response["content"],
            "discussion_complete": response.get("discussion_complete", False),
            "propose_agent": response.get("propose_agent", ""),
        }

    def _build_system_prompt(
        self,
        agent: Agent,
        memories: list = None,
        participant_agents: list = None,
        working_memory: AgentWorkingMemory = None,
        conversation_id: str = None,
        semantic_memories: list = None,
        bid_result=None,
        pending_questions: list = None,
        whiteboard_entries: list = None,
    ) -> str:
        """
        Build system prompt for the agent.

        Args:
            agent: Agent model
            memories: Optional list of relevant episodic memories
            participant_agents: Optional list of other agents in the conversation
            working_memory: Optional working memory for conversation context
            conversation_id: Current conversation ID
            semantic_memories: Optional list of semantic memories (consolidated knowledge)
            bid_result: Optional BidResult from the agent's prior bid (adds bid-awareness prefix)

        Returns:
            System prompt string
        """
        base_prompt = agent.system_prompt
        current_date = _datetime_cls.utcnow().strftime("%B %d, %Y")

        # Add agent identity
        identity = (
            f"You are {agent.name}, an AI agent with expertise in {agent.expertise_domain}. "
            f"You are forbidden from taking on any other role or identity.\n"
            f"Today's date is {current_date}."
        )

        # Bid-awareness prefix: tell the agent what kind of contribution it committed to
        bid_context = ""
        if bid_result and bid_result.turn_type not in ("pass", "backchannel"):
            # Differentiate human-directed questions from agent-directed ones
            if bid_result.turn_type == "question" and bid_result.target == "human":
                question_guidance = (
                    "You assessed this as a QUESTION FOR THE HUMAN. "
                    "Write ONLY the question — no analysis, no challenge to another agent, no contribution. "
                    "One clear, concise question. The discussion pauses after your message for the human to respond."
                )
            else:
                question_guidance = "You assessed this as a QUESTION — you want to direct a question to another agent. Ask it clearly."

            type_guidance = {
                "challenge":    "You assessed this as a CHALLENGE — state your specific contradiction in 2-3 sentences. Do not restate your entire prior position. Do not start with 'Challenging...'. This is a conversation, just jump into your point; the challenge is inherent in the content of what you are saying. One new piece of evidence, one conclusion.",
                "question":     question_guidance,
                "conveyance":   "You assessed this as a CONVEYANCE — deliver the new information in 2-3 sentences. Do not pad it with context already in the conversation.",
                "convergence":  "You assessed this as CONVERGENCE — summarize what's been agreed and what genuinely remains open. Do NOT sneak in another challenge. Your job is to close, not to win.",
            }
            guidance = type_guidance.get(bid_result.turn_type, "")
            preview_note = f" Your previewed contribution: \"{bid_result.preview}\"." if bid_result.preview else ""
            bid_context = f"\n\n## Your Turn Commitment\n\n{guidance}{preview_note}\n"

        # Add conversation context based on whether this is a solo or multi-agent conversation
        collaboration_context = ""
        if participant_agents and len(participant_agents) == 1:
            # Solo agent: direct conversation with the human
            collaboration_context = "\n\n## Conversation Guidelines\n\n"
            collaboration_context += "You are the only AI agent in this conversation, speaking directly with the human user.\n"
            collaboration_context += "If you are assigned a task, complete it fully in your response — deliver the result, don't just describe what you'd do.\n"
            collaboration_context += "\n**Response length:** Be as thorough as needed for the question. Short questions get short answers; complex tasks get detailed ones.\n"
            collaboration_context += "\n**Adding agents:** Use the `propose_agent` field in your respond tool to suggest adding a new expert. " \
                "Describe the expertise gap and the system will search, vote, and add them automatically.\n"

        elif participant_agents and len(participant_agents) > 1:
            other_agents = [a for a in participant_agents if a.id != agent.id]
            if other_agents:
                agent_names = ", ".join([a.name for a in other_agents])
                collaboration_context = "\n\n## Collaborative Discussion\n\n"
                collaboration_context += f"You are in a discussion with other AI agents: {agent_names}.\n"
                collaboration_context += "**Your primary audience is the other agents — address them directly, not the human.** "
                collaboration_context += "The human is observing and will re-enter when they choose, or when you explicitly ask for their input via a dedicated question turn.\n"
                collaboration_context += "Refer to the human as their name if you know it. Otherwise, refer to them as 'the user'.\n"
                collaboration_context += "\n**How to engage with the other agents:**\n"
                collaboration_context += "- **Speak TO them by name:** 'I agree with [Name] on X, but...' or '[Name], your assumption about Y misses Z because...'\n"
                collaboration_context += "- **Challenge directly:** If you see a gap or contradiction in what another agent said, address *them*, not the topic in the abstract. Speak naturally. Don't just start with 'Challenging [NAME].'\n"
                collaboration_context += "- **Build, don't re-explain:** If an agent already made your point, extend it — don't restate it to show you understood.\n"
                collaboration_context += "- **If it's covered:** Stop. Don't restate it from your domain angle — that's noise.\n"
                collaboration_context += "\n**Novelty test:** Before writing, ask: is the core of what I'm about to say already in the conversation? "
                collaboration_context += "If yes, find the one genuinely new thing you can add — and say only that. If there's nothing new, say nothing.\n"
                collaboration_context += "\n**One job per turn:** Either contribute to the agent discussion OR ask the human a question — not both in the same message. "
                collaboration_context += "Mixing analysis with a human-directed question pollutes both. Keep them separate.\n"
                collaboration_context += "\n**Length: 3–5 sentences for discussion turns.** You're making a single move in a dialogue, not writing an analysis. "
                collaboration_context += "One point, briefly stated. If you can't say it in 5 sentences, you're trying to do too many things at once.\n"
                collaboration_context += "When responding to a challenge: address only the specific challenge. Do NOT restate your entire prior argument. "
                collaboration_context += "One piece of new evidence, one conclusion. Elaborating more doesn't make you more right.\n"
                collaboration_context += "**Exception — deliverables:** If you are assigned a concrete artifact (draft, spec, plan, analysis, listings, etc.), produce the full deliverable in that same response. "
                collaboration_context += "Do NOT confirm you will do it — just do it. A confirmation that produces no artifact is a failed turn.\n"
                collaboration_context += "\n**Reading the conversation:** Messages from other agents appear as '[AgentName]: message'. Your own prior messages have NO prefix.\n"
                collaboration_context += "\n**Note:** The discussion flow is managed by the system — focus entirely on your contribution.\n"
                collaboration_context += "\n**Adding or removing participants:** Use the `propose_agent` field in your respond tool to suggest adding a new expert. " \
                    "Describe the expertise gap and the system will search, vote, and add them automatically. " \
                    "Do not ask for group permission first — filling the field IS the action.\n"

        # Add participation criteria if available
        criteria = ""
        if agent.participation_criteria:
            criteria = "\n\nYour participation criteria:\n"
            for key, value in agent.participation_criteria.items():
                criteria += f"- {key}: {value}\n"

        # Add working memory context (current conversation state)
        working_context = ""
        if working_memory:
            working_context = "\n\n## Current Conversation Context\n"

            # Add current goals
            if working_memory.current_goals and len(working_memory.current_goals) > 0:
                working_context += "Current objectives:\n"
                for goal in working_memory.current_goals:
                    working_context += f"- {goal}\n"

            # Add active constraints
            if working_memory.active_constraints and len(working_memory.active_constraints) > 0:
                working_context += "\nActive constraints:\n"
                for constraint in working_memory.active_constraints:
                    working_context += f"- {constraint}\n"

            # Add conversation-specific context
            if working_memory.conversation_contexts and conversation_id:
                conv_context = working_memory.conversation_contexts.get(conversation_id, {})
                if conv_context.get("topic"):
                    working_context += f"\nConversation focus: {conv_context['topic']}\n"

        # Add semantic memories (consolidated knowledge - always present)
        semantic_context = ""
        if semantic_memories and len(semantic_memories) > 0:
            semantic_context = "\n\n## What You Know About the User\n"

            # Group by category
            preferences = [m for m in semantic_memories if m.category == "preference"]
            rules = [m for m in semantic_memories if m.category == "rule"]
            concepts = [m for m in semantic_memories if m.category == "concept"]
            relationships = [m for m in semantic_memories if m.category == "relationship"]

            if preferences:
                semantic_context += "Preferences:\n"
                for mem in preferences:
                    semantic_context += f"- {mem.key}: {mem.value}\n"

            if rules:
                semantic_context += "\nRules:\n"
                for mem in rules:
                    semantic_context += f"- {mem.value}\n"

            if concepts:
                semantic_context += "\nContext:\n"
                for mem in concepts:
                    semantic_context += f"- {mem.key}: {mem.value}\n"

            if relationships:
                semantic_context += "\nRelationships:\n"
                for mem in relationships:
                    semantic_context += f"- {mem.value}\n"

        # Add relevant episodic memories — split by decay class for temporal orientation
        episodic_context = ""
        if memories:
            history_memories = [
                m for m in memories
                if m.memory_type in settings.memory_immutable_types
            ]
            current_memories = [
                m for m in memories
                if m.memory_type not in settings.memory_immutable_types
            ]
            # History: oldest first; current: newest first
            history_memories.sort(key=lambda m: m.valid_from or m.created_at)
            current_memories.sort(key=lambda m: m.valid_from or m.created_at, reverse=True)

            episodic_context = "\n\n## Memory Context\n"

            if history_memories:
                episodic_context += "\n**Project history** (decisions, rejections, events — permanent records):\n"
                for m in history_memories:
                    time_label = self._format_relative_time(m.valid_from or m.created_at)
                    imp_label = f" | importance:{m.importance}" if m.importance else ""
                    episodic_context += f"- [{time_label} | {m.memory_type}{imp_label}] {m.content}\n"

            if current_memories:
                episodic_context += "\n**Current state** (recent facts and preferences):\n"
                for m in current_memories:
                    time_label = self._format_relative_time(m.valid_from or m.created_at)
                    episodic_context += f"- [{time_label} | {m.memory_type}] {m.content}\n"

            episodic_context += (
                "\nWhen drawing on memory: note the time qualifier of each item. "
                "History items are permanent records — never assume they are outdated. "
                "Current state items reflect conditions as of the stated time — "
                "newer information in this conversation takes precedence.\n"
            )

        # Shared whiteboard — visible to every agent every turn
        whiteboard_context = ""
        if whiteboard_entries:
            whiteboard_context = "\n\n## Shared Whiteboard\n"
            for entry in whiteboard_entries:
                author = entry.last_author_name or "Unknown"
                whiteboard_context += f"[{entry.entry_type}] {entry.key}: {entry.value}  — {author}\n"
            whiteboard_context += (
                "\nUse the `whiteboard_updates` field in your respond() call to add, overwrite, "
                "or remove entries AFTER your message. Values must be ≤240 characters. "
                "Include a reason for every change. Entry types: goal, decision, constraint, open_question, strategy. "
                "Use whiteboard_history() before responding if you need to trace prior reasoning on a topic."
            )

        # Pending human questions context
        # Tells agents what's already been asked so they don't pile on the same question
        pending_q_context = ""
        if pending_questions:
            pending_q_context = "\n\n## Pending Questions for the Human\n"
            pending_q_context += "The following question(s) have already been asked to the human and are awaiting their response:\n"
            for q in pending_questions:
                pending_q_context += f"- {q[:300]}\n"
            pending_q_context += (
                "\nDo NOT ask the human the same question again. "
                "If these questions are blocking you from contributing, state your assumption explicitly "
                "('Assuming [X]...') and proceed — or bid 'pass' and let the discussion wait."
            )

        # Combine all sections
        full_prompt = f"{identity}{collaboration_context}{bid_context}\n\n{base_prompt}{criteria}{working_context}{semantic_context}{episodic_context}{whiteboard_context}{pending_q_context}"

        return full_prompt

    async def _run_whiteboard_history_loop(
        self,
        agent: "Agent",
        system_prompt: str,
        claude_messages: list,
        conversation_id: str,
        db: "AsyncSession",
        max_iterations: int = 3,
    ) -> list:
        """
        Pre-response whiteboard history lookup loop (Claude models only).

        Gives agents the option to query whiteboard_history() before composing
        their main respond() call. Returns an enriched claude_messages list.
        """
        import anthropic

        whiteboard_history_tool = {
            "name": "whiteboard_history",
            "description": (
                "Look up the change history for a whiteboard entry. "
                "Call this before responding if you need to understand how an entry evolved."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "The whiteboard entry key to look up history for.",
                    }
                },
                "required": ["key"],
            },
        }

        messages = list(claude_messages)
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        model = agent.model or settings.default_claude_model

        for _ in range(max_iterations):
            try:
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: client.messages.create(
                        model=model,
                        system=system_prompt,
                        messages=messages,
                        max_tokens=512,
                        temperature=1.0,
                        tools=[whiteboard_history_tool],
                        tool_choice={"type": "auto"},
                    ),
                )
            except Exception as e:
                logger.warning("whiteboard_history_loop_error", error=str(e))
                break

            # If agent chose not to use the tool, we're done
            if response.stop_reason != "tool_use":
                # If the agent produced text, append it so the main call has context
                text_parts = [b.text for b in response.content if hasattr(b, "text")]
                if text_parts:
                    messages.append({"role": "assistant", "content": "\n".join(text_parts)})
                break

            # Extract tool call
            tool_use_block = next(
                (b for b in response.content if b.type == "tool_use" and b.name == "whiteboard_history"),
                None,
            )
            if not tool_use_block:
                break

            key = tool_use_block.input.get("key", "")
            tool_use_id = tool_use_block.id

            # Fetch history
            try:
                history = await whiteboard_service.get_history(conversation_id, db, key=key or None)
                if history:
                    history_lines = []
                    for log in history:
                        ts = log.created_at.strftime("%Y-%m-%d") if log.created_at else "?"
                        if log.action == "set":
                            history_lines.append(
                                f"[{ts}] SET by {log.author_name}: \"{log.new_value}\" — {log.reason}"
                            )
                        else:
                            history_lines.append(
                                f"[{ts}] REMOVED by {log.author_name} — {log.reason}"
                            )
                    history_text = f"History for '{key}':\n" + "\n".join(history_lines)
                else:
                    history_text = f"No history found for key '{key}'."
            except Exception as e:
                history_text = f"Error fetching history: {e}"

            # Append tool use + result to messages
            messages.append({
                "role": "assistant",
                "content": response.content,
            })
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": history_text,
                    }
                ],
            })

        return messages

    async def _broadcast_whiteboard_change(
        self,
        conversation_id: str,
        entries: list,
        updates: list,
        agent_name: str,
        db: "AsyncSession",
    ) -> None:
        """Broadcast whiteboard.updated WS event and save a system message after agent updates."""
        from app.schemas.message import MessageResponse

        def _entry_to_dict(e):
            return {
                "id": e.id,
                "key": e.key,
                "entry_type": e.entry_type,
                "value": e.value,
                "last_author_name": e.last_author_name,
                "last_author_type": e.last_author_type,
                "updated_at": e.updated_at.isoformat() if e.updated_at else None,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }

        # Build a summary of all changes for the system message
        parts = []
        for upd in updates:
            action = upd.get("action", "updated")
            key = upd.get("key", "")
            entry_type = upd.get("entry_type", "")
            value = upd.get("value", "")
            if action == "set":
                parts.append(f'[{entry_type}] {key}: "{value}"')
            else:
                parts.append(f'removed {key}')

        change_summary = "; ".join(parts) if parts else "updated whiteboard"
        content = f"{agent_name} updated whiteboard: {change_summary}"

        # Use first update as the "change" payload for WS event
        first = updates[0] if updates else {}
        change = {
            "action": first.get("action", "set"),
            "key": first.get("key", ""),
            "entry_type": first.get("entry_type", ""),
            "value": first.get("value", ""),
            "author_name": agent_name,
            "author_type": "agent",
            "reason": first.get("reason", ""),
            "count": len(updates),
        }

        # Broadcast WS event
        await ws_manager.send_whiteboard_event(
            conversation_id=conversation_id,
            entries=[_entry_to_dict(e) for e in entries],
            change=change,
        )

        # Save system message
        msg = Message(
            conversation_id=conversation_id,
            sender_type="system",
            sender_id=None,
            content=content,
            requires_human_decision=False,
            decision_resolved=False,
            extra_data={"whiteboard_change": change},
        )
        db.add(msg)
        await db.commit()
        await db.refresh(msg)

        await ws_manager.send_message_event(
            conversation_id,
            MessageResponse(**msg.__dict__, sender_name=None).model_dump(mode="json"),
        )

    @staticmethod
    def _format_relative_time(dt: datetime) -> str:
        """Return a human-readable relative time string for a datetime."""
        days = max(0, (_datetime_cls.utcnow() - dt).days)
        if days == 0:
            return "today"
        if days < 7:
            return f"{days} day{'s' if days > 1 else ''} ago"
        if days < 30:
            weeks = days // 7
            return f"{weeks} week{'s' if weeks > 1 else ''} ago"
        if days < 365:
            months = days // 30
            return f"{months} month{'s' if months > 1 else ''} ago"
        years = days // 365
        return f"over {years} year{'s' if years > 1 else ''} ago"

    @staticmethod
    def _merge_consecutive_messages(messages: list) -> list:
        """Merge consecutive messages with the same role (required by Claude/OpenAI APIs)."""
        if not messages:
            return messages

        merged = [messages[0].copy()]
        for msg in messages[1:]:
            if msg["role"] == merged[-1]["role"]:
                merged[-1]["content"] += "\n\n" + msg["content"]
            else:
                merged.append(msg.copy())
        return merged


# Singleton instance
agent_service = AgentService()
