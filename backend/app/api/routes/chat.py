"""Chat and agent response routes."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import List

from app.database import get_db, AsyncSessionLocal
from app.models import Message, Conversation, Agent, ConversationParticipant, SessionState
from app.schemas import MessageResponse
from app.services.agent_service import agent_service
from app.services.speaker_selection import speaker_selection_service  # deprecated — kept for audit
from app.services import interrupt_state
from app.services import bid_service
from app.services import facilitation
from app.services.websocket_manager import ws_manager
import structlog
import asyncio
from datetime import datetime

logger = structlog.get_logger()
router = APIRouter()


async def _run_reflection_for_agent(agent_id: str, conversation_id: str) -> None:
    """Background task: generate prospective reflection summaries for one agent.

    Opens its own DB session so it can run safely after the request session closes.
    """
    from app.services.memory_service import memory_service
    try:
        async with AsyncSessionLocal() as session:
            await memory_service.trigger_prospective_reflection(
                agent_id=agent_id,
                conversation_id=conversation_id,
                db=session,
            )
            await session.commit()
    except Exception as e:
        logger.error(
            "background_reflection_error",
            agent_id=agent_id,
            conversation_id=conversation_id,
            error=str(e),
        )


class TriggerAgentRequest(BaseModel):
    """Request to trigger an agent response."""
    agent_id: str


@router.post("/{conversation_id}/trigger-agent", response_model=MessageResponse)
async def trigger_agent_response(
    conversation_id: str,
    request: TriggerAgentRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Trigger an agent to generate a response in a conversation.

    This endpoint:
    1. Gets the agent and conversation
    2. Generates a response based on conversation history
    3. Saves the response as a new message
    4. Returns the message
    """
    # Verify conversation exists
    conv_result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = conv_result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Verify agent exists
    agent_result = await db.execute(
        select(Agent).where(Agent.id == request.agent_id)
    )
    agent = agent_result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    try:
        # Get all participant agents for context
        participant_ids_result = await db.execute(
            select(ConversationParticipant.agent_id)
            .where(ConversationParticipant.conversation_id == conversation_id)
            .where(ConversationParticipant.is_active == True)
        )
        participant_ids = [row[0] for row in participant_ids_result.all()]

        participant_agents = []
        if participant_ids:
            participant_agents_result = await db.execute(
                select(Agent).where(Agent.id.in_(participant_ids))
            )
            participant_agents = list(participant_agents_result.scalars().all())

        # Generate response
        logger.info(
            "triggering_agent_response",
            conversation_id=conversation_id,
            agent_id=request.agent_id
        )

        # Send typing indicator
        await ws_manager.send_agent_typing(
            conversation_id=conversation_id,
            agent_id=request.agent_id,
            agent_name=agent.name
        )

        response_data = await agent_service.generate_agent_response(
            agent_id=request.agent_id,
            conversation_id=conversation_id,
            db=db,
            participant_agents=participant_agents
        )

        # Save as message
        message = Message(
            conversation_id=conversation_id,
            sender_type="agent",
            sender_id=request.agent_id,
            content=response_data["content"]
        )
        db.add(message)
        await db.commit()
        await db.refresh(message)

        logger.info(
            "agent_response_saved",
            message_id=message.id,
            agent_id=request.agent_id
        )

        # Build response
        response = MessageResponse(
            **message.__dict__,
            sender_name=agent.name
        )

        # Broadcast via WebSocket
        await ws_manager.send_message_event(
            conversation_id,
            response.model_dump(mode='json')
        )

        return response

    except Exception as e:
        logger.error("agent_response_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


def check_interrupt(conversation_id: str, db=None) -> bool:
    """Check if user has requested to interrupt the discussion (in-memory, instant)."""
    return interrupt_state.check_interrupt(conversation_id)


def _is_duplicate_response(new_content: str, prior_content: str, threshold: float = 0.85) -> bool:
    """
    Return True if new_content is essentially the same as prior_content.

    Normalizes whitespace and case, then computes a similarity ratio using
    difflib. A threshold of 0.85 catches rephrased-but-identical closing
    statements while leaving genuinely different messages alone.
    """
    from difflib import SequenceMatcher

    def normalize(s: str) -> str:
        return " ".join(s.lower().split())

    a = normalize(new_content)
    b = normalize(prior_content)
    if not a or not b:
        return False
    return SequenceMatcher(None, a, b).ratio() >= threshold


def parse_agent_mentions(message_content: str, participant_agents: list) -> List[str]:
    """
    Parse @mentions from message content and match to agent names.

    Args:
        message_content: The message text to parse
        participant_agents: List of Agent objects in the conversation

    Returns:
        List of agent IDs that were mentioned

    Examples:
        "@SaaS Growth what do you think?" -> [saas_growth_id]
        "@Product/UX can you help?" -> [product_ux_id]
        "Hey @Alice and @Bob, thoughts?" -> [alice_id, bob_id]
        '@"Agent Name" for quoted names' -> [agent_name_id]
    """
    import re

    mentioned_agent_ids = []

    # Find all @ positions in the message
    at_positions = [i for i, char in enumerate(message_content) if char == '@']

    for pos in at_positions:
        # Extract text after @
        remaining_text = message_content[pos+1:]

        # Check if it's a quoted mention: @"Agent Name"
        quoted_match = re.match(r'^"([^"]+)"', remaining_text)
        if quoted_match:
            mention_text = quoted_match.group(1).strip()
        else:
            # Not quoted - try to match progressively longer strings against agent names
            # Extract up to next punctuation or special char (but allow /)
            unquoted_match = re.match(r'^([A-Za-z0-9/\s\-]+)', remaining_text)
            if not unquoted_match:
                continue

            potential_mention = unquoted_match.group(1).strip()

            # Try to match the longest possible agent name from this text
            # Sort agents by name length (longest first) for better matching
            best_match = None
            best_match_length = 0

            for agent in participant_agents:
                agent_name_lower = agent.name.lower()
                potential_lower = potential_mention.lower()

                # Check if the potential mention starts with this agent's name
                if potential_lower.startswith(agent_name_lower):
                    if len(agent_name_lower) > best_match_length:
                        best_match = agent
                        best_match_length = len(agent_name_lower)

            if best_match and best_match.id not in mentioned_agent_ids:
                mentioned_agent_ids.append(best_match.id)
            continue

        # For quoted mentions, try exact or fuzzy match
        mention_lower = mention_text.lower()
        for agent in participant_agents:
            agent_name_lower = agent.name.lower()

            if mention_lower == agent_name_lower or mention_lower in agent_name_lower:
                if agent.id not in mentioned_agent_ids:
                    mentioned_agent_ids.append(agent.id)
                break

    return mentioned_agent_ids


async def get_recent_speakers(
    conversation_id: str,
    db: AsyncSession,
    count: int = 1
) -> List[str]:
    """
    Get the last N unique agent speakers in a conversation.

    Default is 1 (only the immediate last speaker) to allow natural back-and-forth:
    - Agent A speaks → Agent B speaks → Agent A can speak again ✓
    - Agent A speaks → Agent A speaks again ✗ (blocked)

    Args:
        conversation_id: Conversation ID
        db: Database session
        count: Number of recent speakers to retrieve (default: 1 for immediate last speaker only)

    Returns:
        List of agent IDs in order (most recent first)
    """
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .where(Message.sender_type == "agent")
        .order_by(Message.created_at.desc())
        .limit(count * 2)  # Fetch more than needed to account for same agent multiple times
    )
    recent_messages = result.scalars().all()

    # Return unique agent IDs in order, most recent first
    seen = set()
    speakers = []
    for msg in recent_messages:
        if msg.sender_id and msg.sender_id not in seen:
            seen.add(msg.sender_id)
            speakers.append(msg.sender_id)
            if len(speakers) >= count:
                break

    return speakers


async def run_discussion_flow(
    conversation_id: str,
    db: AsyncSession,
    max_parallel_initial: int = 1,
    max_sequential_followups: int = 20,
    initial_threshold: float = 0.4,
    followup_threshold: float = 0.4
):
    """
    Run a hybrid conversational flow where agents can respond to each other.

    1:1 Mode: Single agent responds once, then returns control to human
    Multi-agent Mode:
        Round 1: Single initial responder (1 agent)
        Round 2+: Sequential responses (thoughtful back-and-forth, up to 20 follow-ups)

    This creates natural discussion bursts after human messages.
    """
    participant_ids_result = await db.execute(
        select(ConversationParticipant.agent_id)
        .where(ConversationParticipant.conversation_id == conversation_id)
        .where(ConversationParticipant.is_active == True)
    )
    participant_ids = [row[0] for row in participant_ids_result.all()]

    if not participant_ids:
        return

    # ===== 1:1 CONVERSATION MODE =====
    # In 1:1 conversations, skip speaker selection and just have the agent respond once
    if len(participant_ids) == 1:
        logger.info("one_on_one_conversation_mode", conversation_id=conversation_id, agent_id=participant_ids[0])

        agent_id = participant_ids[0]

        # Get agent info
        agent_result = await db.execute(
            select(Agent).where(Agent.id == agent_id)
        )
        agent = agent_result.scalar_one_or_none()

        if not agent:
            logger.error("agent_not_found_in_1on1", agent_id=agent_id)
            return

        # Get all participant agents for context (just this one agent)
        participant_agents = [agent]

        try:
            # Send typing indicator
            await ws_manager.send_agent_typing(
                conversation_id=conversation_id,
                agent_id=agent_id,
                agent_name=agent.name
            )

            # Generate single response
            response_data = await agent_service.generate_agent_response(
                agent_id=agent_id,
                conversation_id=conversation_id,
                db=db,
                participant_agents=participant_agents
            )

            if not response_data or not response_data.get("content", "").strip():
                logger.warning("empty_response_in_1on1", agent_id=agent_id)
                return

            # Save message
            message = Message(
                conversation_id=conversation_id,
                sender_type="agent",
                sender_id=agent_id,
                content=response_data["content"]
            )
            db.add(message)
            await db.commit()
            await db.refresh(message)

            # Broadcast via WebSocket
            await ws_manager.send_message_event(
                conversation_id,
                MessageResponse(
                    **message.__dict__,
                    sender_name=agent.name
                ).model_dump(mode='json')
            )

            logger.info("one_on_one_response_complete", agent_name=agent.name)

            # Check if the agent proposed adding someone via the respond tool
            propose_agent_desc = response_data.get("propose_agent", "").strip()
            if propose_agent_desc:
                try:
                    from app.services.proposal_service import proposal_service as _proposal_service
                    all_agents_result = await db.execute(select(Agent))
                    all_agents = list(all_agents_result.scalars().all())

                    session_result = await db.execute(
                        select(SessionState).where(SessionState.conversation_id == conversation_id)
                    )
                    session = session_result.scalar_one_or_none()
                    human_votes_flag = bool(session.human_votes_on_proposals) if session else False

                    await _proposal_service.run_proposal(
                        proposer_id=agent_id,
                        proposal_type="propose_addition",
                        target_agent_id=None,
                        need_description=propose_agent_desc,
                        conversation_id=conversation_id,
                        db=db,
                        participant_agents=participant_agents,
                        participant_ids=participant_ids,
                        all_agents=all_agents,
                        human_votes=human_votes_flag,
                    )

                    # Refresh participant state
                    refreshed_result = await db.execute(
                        select(ConversationParticipant.agent_id)
                        .where(ConversationParticipant.conversation_id == conversation_id)
                        .where(ConversationParticipant.is_active == True)
                    )
                    participant_ids = [row[0] for row in refreshed_result.all()]
                    logger.info(
                        "one_on_one_propose_agent_complete",
                        conversation_id=conversation_id,
                        participant_count=len(participant_ids),
                        description=propose_agent_desc,
                    )
                except Exception as e:
                    logger.error("one_on_one_propose_agent_error", agent_id=agent_id, error=str(e))

            # Notify frontend that discussion is complete
            try:
                await ws_manager.send_debug_event(
                    conversation_id=conversation_id,
                    event_type="discussion_complete",
                    data={
                        "timestamp": datetime.utcnow().isoformat(),
                        "mode": "1:1",
                        "total_responses": 1
                    }
                )
            except Exception as e:
                logger.error("failed_to_send_discussion_complete_event", error=str(e))

            return  # Return control to human

        except Exception as e:
            logger.error("one_on_one_response_error", agent_id=agent_id, error=str(e))
            return

    # Fetch all participant agents for context
    participant_agents_result = await db.execute(
        select(Agent).where(Agent.id.in_(participant_ids))
    )
    participant_agents = list(participant_agents_result.scalars().all())

    # Get or create session state
    session_result = await db.execute(
        select(SessionState).where(SessionState.conversation_id == conversation_id)
    )
    session = session_result.scalar_one_or_none()

    if not session:
        session = SessionState(conversation_id=conversation_id)
        db.add(session)
        await db.flush()

    # Clear interrupt flags (in-memory and DB) at start of new discussion
    interrupt_state.clear_interrupt(conversation_id)
    session.interrupt_requested = False
    await db.commit()

    # ===== ROUND 1: PARALLEL RESPONSES (Fast initial reactions) =====
    logger.info(
        "starting_parallel_round",
        conversation_id=conversation_id,
        max_speakers=max_parallel_initial
    )

    # Get the last message (should be from human)
    last_msg_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    last_message = last_msg_result.scalar_one_or_none()

    if not last_message:
        logger.info("no_message_to_respond_to", conversation_id=conversation_id)
        return

    # Check for @mentions in the message
    mentioned_agent_ids = parse_agent_mentions(last_message.content, participant_agents)

    if mentioned_agent_ids:
        # @mentions found - force these agents to respond (bypass speaker selection)
        logger.info(
            "agent_mentions_detected",
            conversation_id=conversation_id,
            mentioned_agent_ids=mentioned_agent_ids,
            mentioned_agent_names=[a.name for a in participant_agents if a.id in mentioned_agent_ids]
        )
        # Return as list of tuples (agent_id, score=1.0) to match speaker_selection format
        selected_speakers = [(agent_id, 1.0) for agent_id in mentioned_agent_ids]
    else:
        # No @mentions - use normal speaker selection
        responding_to_agent = last_message.sender_type == "agent"
        selected_speakers = await speaker_selection_service.select_speakers(
            conversation_id=conversation_id,
            message_content=last_message.content,
            participant_agent_ids=participant_ids,
            db=db,
            max_speakers=max_parallel_initial,
            min_relevance_score=initial_threshold,
            responding_to_agent=responding_to_agent
        )

    if not selected_speakers:
        # If no agents met threshold, check if this is a human message
        # For human messages, always pick the highest scoring agent (even if below threshold)
        # For agent messages, it's okay to have no response (natural conversation end)
        if not responding_to_agent:
            logger.info(
                "no_agents_met_threshold_picking_highest",
                conversation_id=conversation_id,
                threshold=initial_threshold
            )
            # Re-run selection with threshold=0 to get the highest scorer
            selected_speakers = await speaker_selection_service.select_speakers(
                conversation_id=conversation_id,
                message_content=last_message.content,
                participant_agent_ids=participant_ids,
                db=db,
                max_speakers=1,  # Just pick the single highest scorer
                min_relevance_score=0.0,  # No threshold - get highest
                responding_to_agent=responding_to_agent
            )

            if not selected_speakers:
                # Still no speakers (shouldn't happen, but handle gracefully)
                logger.warning("no_agents_available_at_all", conversation_id=conversation_id)
                return
        else:
            # Responding to agent and no one qualified - natural end of discussion
            logger.info("no_relevant_speakers_for_parallel_round", conversation_id=conversation_id)
            # Notify frontend that discussion is complete (no agents want to respond)
            try:
                await ws_manager.send_debug_event(
                    conversation_id=conversation_id,
                    event_type="discussion_complete",
                    data={
                        "timestamp": datetime.utcnow().isoformat(),
                        "reason": "no_agents_selected",
                        "parallel_responses": 0,
                        "sequential_responses": 0,
                        "total_responses": 0
                    }
                )
            except Exception as e:
                logger.error("failed_to_send_discussion_complete_event", error=str(e))
            return

    # Generate responses in parallel
    async def generate_parallel_response(agent_id: str):
        """Generate response for parallel round."""
        try:
            agent_result = await db.execute(
                select(Agent).where(Agent.id == agent_id)
            )
            agent = agent_result.scalar_one_or_none()
            if not agent:
                return None

            # Send typing indicator
            await ws_manager.send_agent_typing(
                conversation_id=conversation_id,
                agent_id=agent_id,
                agent_name=agent.name
            )

            response_data = await agent_service.generate_agent_response(
                agent_id=agent_id,
                conversation_id=conversation_id,
                db=db,
                participant_agents=participant_agents
            )

            if not response_data or not response_data.get("content", "").strip():
                logger.warning("agent_empty_response", agent_id=agent_id, agent_name=agent.name)
                return None

            return {
                "agent_id": agent_id,
                "agent_name": agent.name,
                "content": response_data["content"],
                "discussion_complete": response_data.get("discussion_complete", False)
            }
        except Exception as e:
            logger.error("parallel_response_error", agent_id=agent_id, error=str(e))
            return None

    logger.info("generating_parallel_responses", agent_count=len(selected_speakers))
    tasks = [generate_parallel_response(agent_id) for agent_id, _ in selected_speakers]
    results = await asyncio.gather(*tasks)

    # Save parallel responses and check for completion signals
    parallel_responses = 0
    any_agent_signaled_complete = False

    for result in results:
        if result is None:
            continue

        # Check for interrupt before saving each parallel response
        if check_interrupt(conversation_id):
            logger.info("discussion_interrupted_during_parallel_save", conversation_id=conversation_id)
            await db.commit()  # Commit any messages saved so far
            interrupt_state.clear_interrupt(conversation_id)
            try:
                await ws_manager.send_debug_event(
                    conversation_id=conversation_id,
                    event_type="discussion_complete",
                    data={"timestamp": datetime.utcnow().isoformat(), "reason": "user_interrupt"}
                )
            except Exception:
                pass
            return

        # Check if agent signaled discussion complete
        if result.get("discussion_complete", False):
            any_agent_signaled_complete = True
            logger.info(
                "agent_signaled_discussion_complete",
                agent_name=result["agent_name"],
                conversation_id=conversation_id
            )

        message = Message(
            conversation_id=conversation_id,
            sender_type="agent",
            sender_id=result["agent_id"],
            content=result["content"]
        )
        db.add(message)
        await db.flush()

        await ws_manager.send_message_event(
            conversation_id,
            MessageResponse(
                **message.__dict__,
                sender_name=result["agent_name"]
            ).model_dump(mode='json')
        )
        parallel_responses += 1

    await db.commit()

    # Check for interrupt
    if check_interrupt(conversation_id):
        logger.info("discussion_interrupted_after_parallel", conversation_id=conversation_id)
        interrupt_state.clear_interrupt(conversation_id)
        try:
            await ws_manager.send_debug_event(
                conversation_id=conversation_id,
                event_type="discussion_complete",
                data={"timestamp": datetime.utcnow().isoformat(), "reason": "user_interrupt"}
            )
        except Exception:
            pass
        return

    logger.info(
        "parallel_round_complete",
        conversation_id=conversation_id,
        responses_generated=parallel_responses
    )

    if parallel_responses == 0:
        logger.info("no_parallel_responses_generated", conversation_id=conversation_id)
        # Send discussion complete event
        try:
            await ws_manager.send_debug_event(
                conversation_id=conversation_id,
                event_type="discussion_complete",
                data={
                    "timestamp": datetime.utcnow().isoformat(),
                    "reason": "no_agents_responded",
                    "parallel_responses": 0,
                    "sequential_responses": 0,
                    "total_responses": 0
                }
            )
        except Exception as e:
            logger.error("failed_to_send_discussion_complete_event", error=str(e))
        return

    # ===== DISCUSSION STATE TRACKING =====
    # Track turn counts per agent for bid-based variance/dominance calculations
    agent_turn_counts = {agent_id: 0 for agent_id in participant_ids}
    unique_speakers: set = set()

    discussion_state = {
        "turn": 1,  # Starting at 1 (parallel round completed)
        "unique_speakers": unique_speakers,
        "last_speakers": [],  # Last 2 speakers
        "any_completion_signals": 0,  # Legacy — kept for logging
    }

    # Track who spoke in parallel round
    for result in results:
        if result is not None:
            discussion_state["unique_speakers"].add(result["agent_id"])
            discussion_state["last_speakers"].append(result["agent_id"])
            agent_turn_counts[result["agent_id"]] = agent_turn_counts.get(result["agent_id"], 0) + 1

    logger.info(
        "parallel_round_complete_continuing",
        conversation_id=conversation_id,
        parallel_responses=parallel_responses,
        unique_speakers=len(discussion_state["unique_speakers"]),
    )

    # ===== ROUND 2+: BID-BASED LOOP =====
    logger.info(
        "starting_bid_based_rounds",
        conversation_id=conversation_id,
        max_followups=max_sequential_followups,
    )

    # Query all agents for proposal context (available to add) — done once per discussion flow
    all_agents_result = await db.execute(select(Agent))
    all_agents = list(all_agents_result.scalars().all())

    sequential_count = 0
    close_reason = ""
    rule2b_counts: dict = {}  # {agent_id: int} — tracks Rule-2b routings per agent
    try:
        for followup_num in range(max_sequential_followups):
            discussion_state["turn"] += 1

            # Check interrupt (in-memory dict lookup — no DB hit)
            if interrupt_state.check_interrupt(conversation_id):
                close_reason = "user_interrupt"
                logger.info("sequential_ended_user_interrupt", followup_num=followup_num)
                break

            # Fetch recent messages for bid context (lightweight — max 20)
            ctx_result = await db.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at.desc())
                .limit(20)
            )
            context_messages = list(reversed(ctx_result.scalars().all()))

            # Compute pending human questions and question-debt state
            pending_human_questions = facilitation.get_pending_human_questions(context_messages)
            turns_since_human = facilitation.count_agent_turns_since_human(context_messages)
            # Question debt: 3+ agent turns with an unanswered human question
            # Agents will be told to assume an answer and proceed rather than re-asking
            question_debt = turns_since_human >= 3 and len(pending_human_questions) > 0

            if question_debt:
                logger.info(
                    "question_debt_detected",
                    pending_count=len(pending_human_questions),
                    turns_since_human=turns_since_human,
                )

            # Step 1: @mentions in recent messages force those agents next
            forced_speakers = facilitation.get_recent_mentions(
                context_messages[-3:], participant_agents, lookback=3
            )

            # Step 2: Collect bids in parallel (all agents simultaneously)
            bids = await bid_service.collect_bids(
                agent_ids=participant_ids,
                conversation_id=conversation_id,
                db=db,
                context_messages=context_messages,
                participant_agents=participant_agents,
                pending_questions=pending_human_questions,
                available_agents=all_agents,
            )

            # Check for proposal bids — handle before normal flow
            from app.services.bid_service import PROPOSAL_TYPES
            from app.services.proposal_service import proposal_service as _proposal_service

            proposal_bids = [b for b in bids if b.turn_type in PROPOSAL_TYPES]
            if proposal_bids:
                # Pick highest-confidence proposal bid
                top_proposal = sorted(proposal_bids, key=lambda b: b.confidence, reverse=True)[0]
                human_votes_flag = bool(session.human_votes_on_proposals)
                await _proposal_service.run_proposal(
                    proposer_id=top_proposal.agent_id,
                    proposal_type=top_proposal.turn_type,
                    # For removals: target is the agent_id in the bid.
                    # For additions: target may be None — elaboration step finds it.
                    target_agent_id=top_proposal.target or None,
                    need_description=top_proposal.preview or "",
                    conversation_id=conversation_id,
                    db=db,
                    participant_agents=participant_agents,
                    participant_ids=participant_ids,
                    all_agents=all_agents,
                    human_votes=human_votes_flag,
                )
                # Refresh participant list — agent may have been added or removed
                participant_ids_result = await db.execute(
                    select(ConversationParticipant.agent_id)
                    .where(ConversationParticipant.conversation_id == conversation_id)
                    .where(ConversationParticipant.is_active == True)
                )
                participant_ids = [row[0] for row in participant_ids_result.all()]
                participant_agents_result = await db.execute(
                    select(Agent).where(Agent.id.in_(participant_ids))
                )
                participant_agents = list(participant_agents_result.scalars().all())
                continue  # Skip normal response generation for this turn

            # Step 3a: Check distributed closure conditions
            should_close, bid_close_reason = bid_service.check_closure(bids)
            if should_close:
                close_reason = bid_close_reason
                logger.info(
                    "discussion_closed_by_bids",
                    reason=close_reason,
                    followup_num=followup_num,
                )
                break

            # Step 3b: Check interrupt again (could have arrived during bid collection)
            if interrupt_state.check_interrupt(conversation_id):
                close_reason = "user_interrupt"
                logger.info("sequential_ended_user_interrupt_after_bids", followup_num=followup_num)
                break

            # Step 4: Apply facilitation rules to pick speakers
            last_speakers = discussion_state["last_speakers"]
            last_speaker_id = last_speakers[-1] if last_speakers else None
            selected_agent_ids = bid_service.select_speakers(
                bids=bids,
                recent_turn_counts=agent_turn_counts,
                mentioned_agents=forced_speakers,
                last_speaker_id=last_speaker_id,
                rule2b_counts=rule2b_counts,
            )

            if not selected_agent_ids:
                close_reason = "no_speakers_selected"
                logger.info("no_speakers_selected_natural_closure", followup_num=followup_num)
                break

            # Build bid lookup for passing to generate_agent_response
            bid_map = {b.agent_id: b for b in bids}

            # Check if any selected speaker is directing their question at the human.
            # If so, we let them speak (to formulate the question), then return control.
            defer_to_human = bid_service.has_human_questions(bids, selected_agent_ids)
            if defer_to_human:
                logger.info(
                    "human_input_requested_by_agent",
                    followup_num=followup_num,
                    agents=selected_agent_ids,
                )

            # Step 5: Generate responses for each selected agent
            for agent_id in selected_agent_ids:
                if interrupt_state.check_interrupt(conversation_id):
                    close_reason = "user_interrupt"
                    break

                agent_result = await db.execute(
                    select(Agent).where(Agent.id == agent_id)
                )
                agent = agent_result.scalar_one_or_none()
                if not agent:
                    continue

                bid_result = bid_map.get(agent_id)

                await ws_manager.send_agent_typing(
                    conversation_id=conversation_id,
                    agent_id=agent_id,
                    agent_name=agent.name,
                )

                try:
                    response_data = await agent_service.generate_agent_response(
                        agent_id=agent_id,
                        conversation_id=conversation_id,
                        db=db,
                        participant_agents=participant_agents,
                        bid_result=bid_result,
                        pending_questions=pending_human_questions,
                    )

                    if interrupt_state.check_interrupt(conversation_id):
                        close_reason = "user_interrupt"
                        break

                    response_content = response_data.get("content", "").strip()
                    if not response_content:
                        logger.warning("empty_bid_response", agent_id=agent_id)
                        continue

                    # Hard duplicate guard: if this response is nearly identical to the
                    # agent's most recent prior message, drop it silently. This catches
                    # the "doubles at close" pattern where an agent repeats a convergence
                    # statement it just delivered.
                    prior_messages_this_agent = [
                        m for m in context_messages if m.sender_id == agent_id
                    ]
                    if prior_messages_this_agent:
                        last_content = prior_messages_this_agent[-1].content
                        if _is_duplicate_response(response_content, last_content):
                            logger.info(
                                "duplicate_response_suppressed",
                                agent_id=agent_id,
                                agent_name=agent.name,
                                similarity=">=0.85",
                            )
                            continue

                    # Phase 4: Parse @mentions from agent response
                    # (facilitation.get_recent_mentions picks these up on the next turn)
                    parse_agent_mentions(response_content, participant_agents)

                    # Save with bid metadata in extra_data
                    extra = None
                    if bid_result:
                        extra = {
                            "turn_type": bid_result.turn_type,
                            "bid_confidence": bid_result.confidence,
                            "bid_target": bid_result.target,
                            "bid_preview": bid_result.preview,
                        }

                    message = Message(
                        conversation_id=conversation_id,
                        sender_type="agent",
                        sender_id=agent_id,
                        content=response_content,
                        extra_data=extra,
                    )
                    db.add(message)
                    await db.commit()
                    await db.refresh(message)

                    await ws_manager.send_message_event(
                        conversation_id,
                        MessageResponse(
                            **message.__dict__,
                            sender_name=agent.name,
                        ).model_dump(mode="json"),
                    )

                    # Track state
                    agent_turn_counts[agent_id] = agent_turn_counts.get(agent_id, 0) + 1
                    discussion_state["unique_speakers"].add(agent_id)
                    discussion_state["last_speakers"].append(agent_id)
                    sequential_count += 1

                    logger.info(
                        "sequential_response_generated",
                        followup_num=followup_num,
                        turn=discussion_state["turn"],
                        agent_name=agent.name,
                        turn_type=bid_result.turn_type if bid_result else "unknown",
                        unique_speakers=len(discussion_state["unique_speakers"]),
                        response_preview=response_content[:100],
                    )

                    # Check if the agent proposed adding someone via the respond tool
                    group_propose_desc = response_data.get("propose_agent", "").strip()
                    if group_propose_desc:
                        try:
                            session_result_p = await db.execute(
                                select(SessionState).where(SessionState.conversation_id == conversation_id)
                            )
                            session_p = session_result_p.scalar_one_or_none()
                            human_votes_flag_p = bool(session_p.human_votes_on_proposals) if session_p else False

                            await _proposal_service.run_proposal(
                                proposer_id=agent_id,
                                proposal_type="propose_addition",
                                target_agent_id=None,
                                need_description=group_propose_desc,
                                conversation_id=conversation_id,
                                db=db,
                                participant_agents=participant_agents,
                                participant_ids=participant_ids,
                                all_agents=all_agents,
                                human_votes=human_votes_flag_p,
                            )
                            # Refresh participants after proposal
                            participant_ids_result = await db.execute(
                                select(ConversationParticipant.agent_id)
                                .where(ConversationParticipant.conversation_id == conversation_id)
                                .where(ConversationParticipant.is_active == True)
                            )
                            participant_ids = [row[0] for row in participant_ids_result.all()]
                            participant_agents_result = await db.execute(
                                select(Agent).where(Agent.id.in_(participant_ids))
                            )
                            participant_agents = list(participant_agents_result.scalars().all())
                        except Exception as pe:
                            logger.error("group_propose_agent_error", agent_id=agent_id, error=str(pe))

                except Exception as e:
                    logger.error("sequential_response_error", agent_id=agent_id, error=str(e))
                    continue

            # If we broke out of the inner loop due to interrupt, break outer loop too
            if interrupt_state.check_interrupt(conversation_id):
                close_reason = "user_interrupt"
                break

            # If the selected speakers were asking the human, end the discussion loop
            # so control returns to the user (their messages created above ask the question)
            if defer_to_human:
                close_reason = "human_input_requested"
                logger.info("discussion_closed_human_input_requested", followup_num=followup_num)
                break

    finally:
        # Always clear in-memory interrupt flag when the discussion loop exits
        interrupt_state.clear_interrupt(conversation_id)

    logger.info(
        "discussion_flow_complete",
        conversation_id=conversation_id,
        parallel_responses=parallel_responses,
        sequential_responses=sequential_count,
        total_responses=parallel_responses + sequential_count,
        total_turns=discussion_state["turn"],
        unique_speakers=len(discussion_state["unique_speakers"]),
        close_reason=close_reason,
    )

    # Clear working memory for all participating agents
    try:
        from app.services.memory_service import memory_service
        for agent_id in participant_ids:
            await memory_service.clear_working_memory_conversation(
                agent_id=agent_id,
                conversation_id=conversation_id,
                db=db
            )
    except Exception as e:
        logger.error("failed_to_clear_working_memory", error=str(e))

    # Fire prospective reflection as background tasks (one per agent that spoke)
    speakers = discussion_state.get("unique_speakers", set())
    if speakers:
        for _agent_id in speakers:
            asyncio.create_task(_run_reflection_for_agent(_agent_id, conversation_id))
        logger.info(
            "prospective_reflection_tasks_queued",
            agent_count=len(speakers),
            conversation_id=conversation_id,
        )

    # Notify frontend that discussion is complete
    try:
        await ws_manager.send_debug_event(
            conversation_id=conversation_id,
            event_type="discussion_complete",
            data={
                "timestamp": datetime.utcnow().isoformat(),
                "parallel_responses": parallel_responses,
                "sequential_responses": sequential_count,
                "total_responses": parallel_responses + sequential_count,
                "total_turns": discussion_state["turn"],
                "unique_speakers": len(discussion_state["unique_speakers"]),
                "reason": close_reason or "discussion_complete",
            }
        )
    except Exception as e:
        logger.error("failed_to_send_discussion_complete_event", error=str(e))


@router.post("/{conversation_id}/trigger-multi-agent")
async def trigger_multi_agent_responses(
    conversation_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Trigger conversational flow with multiple agents.

    This endpoint:
    1. Gets the last message
    2. Runs discussion flow where agents can respond to each other
    3. Returns when discussion naturally concludes
    """
    # Verify conversation exists
    conv_result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = conv_result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    try:
        logger.info(
            "starting_discussion_flow",
            conversation_id=conversation_id
        )

        # Run the hybrid discussion flow
        await run_discussion_flow(
            conversation_id=conversation_id,
            db=db,
            max_parallel_initial=1,       # 1 agent responds first (Round 1)
            max_sequential_followups=20,  # Up to n sequential back-and-forth responses (Round 2+)
            initial_threshold=0.55,       # High threshold for initial response (very selective)
            followup_threshold=0.65       # Dynamic thresholds override this (see below)
        )

        return {"message": "Discussion flow completed"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("discussion_flow_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{conversation_id}/start-autonomous")
async def start_autonomous_mode(
    conversation_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Start autonomous conversation mode.

    Agents will automatically respond to each other until max turns reached.
    """
    # Verify conversation exists
    conv_result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = conv_result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    try:
        # Update conversation mode
        conversation.mode = "autonomous"

        # Create or update session state
        session_result = await db.execute(
            select(SessionState).where(SessionState.conversation_id == conversation_id)
        )
        session = session_result.scalar_one_or_none()

        if not session:
            session = SessionState(conversation_id=conversation_id)
            db.add(session)

        session.is_autonomous = True
        session.turn_count = 0
        from datetime import datetime
        session.start_time = datetime.utcnow()
        session.last_activity = datetime.utcnow()

        await db.commit()

        logger.info("autonomous_mode_started", conversation_id=conversation_id)

        # Run autonomous loop in background (don't pass db session - it will create its own)
        asyncio.create_task(run_autonomous_loop(conversation_id))

        return {"message": "Autonomous mode started", "max_turns": conversation.max_autonomous_turns}

    except Exception as e:
        logger.error("autonomous_start_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{conversation_id}/pause")
async def pause_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Pause an autonomous conversation."""
    # Update session state
    session_result = await db.execute(
        select(SessionState).where(SessionState.conversation_id == conversation_id)
    )
    session = session_result.scalar_one_or_none()

    if session:
        session.is_autonomous = False
        from datetime import datetime
        session.paused_at = datetime.utcnow()

    # Update conversation mode
    conv_result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = conv_result.scalar_one_or_none()

    if conversation:
        conversation.mode = "interactive"

    await db.commit()

    return {"message": "Conversation paused"}


@router.post("/{conversation_id}/interrupt")
async def interrupt_discussion(
    conversation_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Interrupt an ongoing agent discussion.

    Sets an in-memory flag (instant, no DB round-trip in the hot path).
    Also writes to SessionState for audit purposes.
    Immediately broadcasts interrupt.acknowledged so the frontend can show "Stopping…".
    """
    # In-memory flag — checked on every turn, no DB hit in the hot path
    interrupt_state.request_interrupt(conversation_id)

    # Audit: also persist to DB (non-blocking for the discussion loop)
    session_result = await db.execute(
        select(SessionState).where(SessionState.conversation_id == conversation_id)
    )
    session = session_result.scalar_one_or_none()
    if not session:
        session = SessionState(conversation_id=conversation_id)
        db.add(session)
    session.interrupt_requested = True
    await db.commit()

    logger.info("discussion_interrupt_requested", conversation_id=conversation_id)

    # Immediate feedback to the frontend — no need to wait for discussion_complete
    try:
        await ws_manager.broadcast_to_conversation(
            conversation_id,
            {"type": "interrupt.acknowledged", "conversation_id": conversation_id}
        )
    except Exception as e:
        logger.error("interrupt_acknowledged_broadcast_error", error=str(e))

    return {"message": "Discussion interrupt requested"}


class RewindRequest(BaseModel):
    """Request to rewind conversation to a specific message."""
    message_id: str


@router.post("/{conversation_id}/summarize")
async def summarize_discussion(
    conversation_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Summarize agent discussion since the last human message.

    Returns a concise bullet-point summary of what the agents discussed.
    """
    conv_result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    if not conv_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Conversation not found")

    msgs_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )
    all_messages = msgs_result.scalars().all()

    # Find index of the last human message
    last_human_idx = None
    for i, msg in enumerate(all_messages):
        if msg.sender_type == "human":
            last_human_idx = i

    if last_human_idx is None:
        raise HTTPException(status_code=400, detail="No human messages found")

    agent_messages = [m for m in all_messages[last_human_idx + 1:] if m.sender_type == "agent"]
    if not agent_messages:
        raise HTTPException(status_code=400, detail="No agent messages since last human message")

    # Look up agent names
    agent_ids = list({m.sender_id for m in agent_messages})
    agents_result = await db.execute(select(Agent).where(Agent.id.in_(agent_ids)))
    agent_name_map = {a.id: a.name for a in agents_result.scalars().all()}

    # Build transcript
    human_msg = all_messages[last_human_idx]
    lines = [f"Human: {human_msg.content}"]
    for msg in agent_messages:
        name = agent_name_map.get(msg.sender_id, "Agent")
        lines.append(f"{name}: {msg.content}")
    transcript = "\n\n".join(lines)

    from app.services.claude_service import claude_service
    try:
        response = claude_service.client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=(
                "You summarize multi-agent AI conversations concisely. "
                "Write 3-5 bullet points covering: the human's question/topic, "
                "key points each agent made, any agreements or disagreements, "
                "and the conclusion or next steps if any."
            ),
            messages=[{
                "role": "user",
                "content": f"Summarize this agent discussion:\n\n{transcript}"
            }]
        )
        summary = response.content[0].text if response.content else "Could not generate summary."
    except Exception as e:
        logger.error("summarize_error", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to generate summary: {str(e)}")

    # Persist summary as a system message so it's visible in history
    summary_message = Message(
        conversation_id=conversation_id,
        sender_type="system",
        content=summary,
        extra_data={"type": "summary"},
    )
    db.add(summary_message)
    await db.commit()
    await db.refresh(summary_message)

    # Broadcast so connected clients receive it in real-time
    await ws_manager.send_message_event(
        conversation_id,
        MessageResponse(**summary_message.__dict__).model_dump(mode="json"),
    )

    return {
        "summary": summary,
        "message": MessageResponse(**summary_message.__dict__).model_dump(mode="json"),
    }


@router.post("/{conversation_id}/rewind")
async def rewind_conversation(
    conversation_id: str,
    request: RewindRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Rewind conversation to a specific message.

    This will delete all messages after the specified message_id,
    allowing the user to continue the conversation from that point.
    """
    # Verify conversation exists
    conv_result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = conv_result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Verify the target message exists and belongs to this conversation
    target_msg_result = await db.execute(
        select(Message)
        .where(Message.id == request.message_id)
        .where(Message.conversation_id == conversation_id)
    )
    target_message = target_msg_result.scalar_one_or_none()
    if not target_message:
        raise HTTPException(status_code=404, detail="Target message not found")

    # Delete all messages created after the target message
    delete_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .where(Message.created_at > target_message.created_at)
    )
    messages_to_delete = delete_result.scalars().all()

    deleted_count = len(messages_to_delete)

    for message in messages_to_delete:
        await db.delete(message)

    await db.commit()

    logger.info(
        "conversation_rewound",
        conversation_id=conversation_id,
        target_message_id=request.message_id,
        deleted_count=deleted_count
    )

    # Notify clients via WebSocket about the rewind
    await ws_manager.send_debug_event(
        conversation_id=conversation_id,
        event_type="conversation_rewound",
        data={
            "timestamp": datetime.utcnow().isoformat(),
            "target_message_id": request.message_id,
            "deleted_count": deleted_count
        }
    )

    return {
        "message": "Conversation rewound successfully",
        "deleted_count": deleted_count,
        "rewind_to_message_id": request.message_id
    }


async def run_autonomous_loop(conversation_id: str):
    """Run autonomous conversation loop."""
    from app.database import AsyncSessionLocal

    # Create a new database session for this background task
    async with AsyncSessionLocal() as db:
        try:
            session_result = await db.execute(
                select(SessionState).where(SessionState.conversation_id == conversation_id)
            )
            session = session_result.scalar_one_or_none()

            conv_result = await db.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
            conversation = conv_result.scalar_one_or_none()

            if not session or not conversation:
                return

            max_turns = conversation.max_autonomous_turns

            while session.is_autonomous and session.turn_count < max_turns:
                # Trigger multi-agent responses
                try:
                    # Get participants
                    participants_result = await db.execute(
                        select(ConversationParticipant.agent_id)
                        .where(ConversationParticipant.conversation_id == conversation_id)
                        .where(ConversationParticipant.is_active == True)
                    )
                    participant_ids = [row[0] for row in participants_result.all()]

                    if not participant_ids:
                        break

                    # Get last message
                    last_msg_result = await db.execute(
                        select(Message)
                        .where(Message.conversation_id == conversation_id)
                        .order_by(Message.created_at.desc())
                        .limit(1)
                    )
                    last_message = last_msg_result.scalar_one_or_none()

                    if not last_message:
                        break

                    # Select and trigger speakers
                    selected_speakers = await speaker_selection_service.select_speakers(
                        conversation_id=conversation_id,
                        message_content=last_message.content,
                        participant_agent_ids=participant_ids,
                        db=db,
                        max_speakers=2,  # Limit in autonomous mode
                        min_relevance_score=0.6,
                        responding_to_agent=last_message.sender_type == "agent"  # Bonus for agent-to-agent
                    )

                    if not selected_speakers:
                        # Notify frontend that discussion is complete (no agents want to respond)
                        try:
                            await ws_manager.send_debug_event(
                                conversation_id=conversation_id,
                                event_type="discussion_complete",
                                data={
                                    "timestamp": datetime.utcnow().isoformat(),
                                    "reason": "no_agents_selected_autonomous",
                                    "turns_completed": session.turn_count
                                }
                            )
                        except Exception as e:
                            logger.error("failed_to_send_discussion_complete_event", error=str(e))
                        break

                    # Generate responses IN PARALLEL
                    async def autonomous_agent_task(agent_id: str):
                        """Generate response for agent in autonomous mode."""
                        try:
                            agent_result = await db.execute(
                                select(Agent).where(Agent.id == agent_id)
                            )
                            agent = agent_result.scalar_one_or_none()
                            if not agent:
                                return None

                            # Send typing indicator
                            await ws_manager.send_agent_typing(
                                conversation_id=conversation_id,
                                agent_id=agent_id,
                                agent_name=agent.name
                            )

                            response_data = await agent_service.generate_agent_response(
                                agent_id=agent_id,
                                conversation_id=conversation_id,
                                db=db
                            )

                            if not response_data or not response_data.get("content", "").strip():
                                return None

                            return {
                                "agent_id": agent_id,
                                "agent_name": agent.name,
                                "content": response_data["content"]
                            }
                        except Exception as e:
                            logger.error(
                                "autonomous_agent_task_error",
                                agent_id=agent_id,
                                error=str(e)
                            )
                            return None

                    # Call all agents in parallel
                    tasks = [autonomous_agent_task(agent_id) for agent_id, _ in selected_speakers]
                    results = await asyncio.gather(*tasks)

                    # Save and broadcast successful responses
                    for result in results:
                        if result is None:
                            continue

                        message = Message(
                            conversation_id=conversation_id,
                            sender_type="agent",
                            sender_id=result["agent_id"],
                            content=result["content"]
                        )
                        db.add(message)
                        await db.flush()

                        # Broadcast
                        await ws_manager.send_message_event(
                            conversation_id,
                            MessageResponse(
                                **message.__dict__,
                                sender_name=result["agent_name"]
                            ).model_dump(mode='json')
                        )

                    # Update session
                    session.turn_count += 1
                    from datetime import datetime
                    session.last_activity = datetime.utcnow()
                    await db.commit()

                    # Refresh session state
                    await db.refresh(session)

                    # Wait between turns
                    await asyncio.sleep(2)

                except Exception as e:
                    logger.error("autonomous_loop_error", error=str(e))
                    break

            # End autonomous mode
            session.is_autonomous = False
            await db.commit()

            logger.info("autonomous_mode_ended", conversation_id=conversation_id, turns=session.turn_count)

            # Notify frontend that autonomous discussion is complete
            try:
                await ws_manager.send_debug_event(
                    conversation_id=conversation_id,
                    event_type="discussion_complete",
                    data={
                        "timestamp": datetime.utcnow().isoformat(),
                        "reason": "autonomous_mode_ended",
                        "turns_completed": session.turn_count,
                        "max_turns": max_turns
                    }
                )
            except Exception as e:
                logger.error("failed_to_send_discussion_complete_event", error=str(e))

        except Exception as e:
            logger.error("autonomous_loop_fatal_error", error=str(e))
