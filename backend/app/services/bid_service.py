"""Bid service for agent collaboration orchestration.

Implements the bid-based speaker selection model:
- Agents independently decide whether to contribute (bid) before generating full responses
- The system facilitates by applying ordered rules to bids rather than scoring agents externally
- Distributed closure: discussion ends when bids indicate readiness (no single agent decides)
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Tuple
import structlog

from app.services.facilitation import compute_turn_variance

logger = structlog.get_logger()

# Turn type constants
TURN_TYPE_CONVEYANCE = "conveyance"
TURN_TYPE_CHALLENGE = "challenge"
TURN_TYPE_QUESTION = "question"
TURN_TYPE_CONVERGENCE = "convergence"
TURN_TYPE_PASS = "pass"
TURN_TYPE_BACKCHANNEL = "backchannel"
TURN_TYPE_PROPOSE_ADDITION = "propose_addition"  # Proposal to add an agent
TURN_TYPE_PROPOSE_REMOVAL  = "propose_removal"   # Proposal to remove an agent

PROPOSAL_TYPES = {TURN_TYPE_PROPOSE_ADDITION, TURN_TYPE_PROPOSE_REMOVAL}

PASSIVE_TYPES = {TURN_TYPE_PASS, TURN_TYPE_BACKCHANNEL}

# Agents holding more than this share of turns get a confidence discount
DOMINANCE_THRESHOLD = 0.375  # 3/8


@dataclass
class BidResult:
    """Result of an agent's bid to participate in the next turn."""
    agent_id: str
    turn_type: str       # One of the TURN_TYPE_* constants
    confidence: float    # 0.0–1.0; 0.0 for pass
    target: Optional[str] = None   # agent_id for directed questions/@mentions
    preview: Optional[str] = None  # One-sentence preview of the contribution


async def collect_bids(
    agent_ids: List[str],
    conversation_id: str,
    db,
    context_messages: list,
    participant_agents: list,
    pending_questions: list = None,
    available_agents: list = None,
) -> List[BidResult]:
    """
    Collect bids from all agents in parallel via asyncio.gather().

    Args:
        agent_ids: Agent IDs that should submit bids
        conversation_id: Current conversation ID
        db: Async database session
        context_messages: Recent messages (lightweight bid context, not full history)
        participant_agents: All participant Agent objects (for name lookup)
        pending_questions: Questions already asked to the human (not yet answered)
        available_agents: All agents in the system (for proposal bid context)

    Returns:
        List of BidResult, one per agent (never raises — failures become pass bids)
    """
    from app.services.agent_service import agent_service

    # Emit "bidding started" before the gather so the frontend has immediate feedback
    try:
        from app.services.websocket_manager import ws_manager
        agent_name_map = {a.id: a.name for a in (participant_agents or [])}
        await ws_manager.send_debug_event(
            conversation_id=conversation_id,
            event_type="bidding_started",
            data={
                "agent_names": [agent_name_map.get(aid, aid[:8]) for aid in agent_ids],
            },
        )
    except Exception:
        pass  # Debug events are best-effort

    tasks = [
        agent_service.generate_agent_bid(
            agent_id=agent_id,
            conversation_id=conversation_id,
            db=db,
            participant_agents=participant_agents,
            context_messages=context_messages,
            pending_questions=pending_questions,
            available_agents=available_agents,
        )
        for agent_id in agent_ids
    ]

    results: List[BidResult] = list(await asyncio.gather(*tasks))

    logger.debug(
        "bids_collected",
        conversation_id=conversation_id,
        bids=[
            {
                "agent_id": b.agent_id,
                "turn_type": b.turn_type,
                "confidence": round(b.confidence, 2),
            }
            for b in results
        ],
    )

    # Emit debug event so the frontend debug console can show bid outcomes
    try:
        from app.services.websocket_manager import ws_manager
        agent_name_map = {a.id: a.name for a in (participant_agents or [])}
        await ws_manager.send_debug_event(
            conversation_id=conversation_id,
            event_type="bids_collected",
            data={
                "bids": [
                    {
                        "agent_name": agent_name_map.get(b.agent_id, b.agent_id[:8]),
                        "turn_type": b.turn_type,
                        "confidence": round(b.confidence, 2),
                        "target": b.target,
                        "preview": b.preview,
                    }
                    for b in results
                ]
            },
        )
    except Exception:
        pass  # Debug events are best-effort

    return results


def select_speakers(
    bids: List[BidResult],
    recent_turn_counts: dict,
    mentioned_agents: List[str],
    last_speaker_id: Optional[str] = None,
    rule2b_counts: Optional[dict] = None,
) -> List[str]:
    """
    Apply facilitation rules (in order) to determine who speaks next.

    Rules:
    1.   @mentions in any recent message → force those agents (bypass bids entirely)
    2a.  question bids with target="human" → let those agents speak, then close
         (has_human_questions() detects this in chat.py and sets close_reason)
    2b.  question bids with a specific agent target → route to that agent
    3.   Remove pass and backchannel from the speaker pool
    3.5. Remove the last speaker from Rule 7 candidates (prevents consecutive self-response)
         Exception: if they are the only remaining active bidder, they may still speak.
    4.   No remaining active bids → natural closure (return [])
    5.   challenge bids sequenced before convergence bids
    6.   Agents holding >37.5% of turns → proportional confidence discount
    7.   Select winner(s) by adjusted confidence:
         - Two concurrent challenges allowed simultaneously
         - Otherwise pick single highest-confidence speaker

    Args:
        bids: BidResult list from collect_bids()
        recent_turn_counts: {agent_id: int} for turn variance calculation
        mentioned_agents: Agent IDs forced by @mentions (already computed by facilitation)
        last_speaker_id: Agent ID who spoke immediately before this round (excluded from Rule 7)

    Returns:
        List of agent IDs to speak next; empty list signals natural closure
    """
    if not bids:
        logger.debug("select_speakers_no_bids")
        return []

    bid_map = {b.agent_id: b for b in bids}

    # Rule 1: @mentions force those agents regardless of their bid
    if mentioned_agents:
        forced = [aid for aid in mentioned_agents if aid in bid_map]
        if forced:
            logger.debug("facilitation_rule_1_mentions_forced", forced=forced)
            return forced

    # Rule 2a: question bids directed at the human ("human" target)
    # → let the asking agents speak so they can formulate their question,
    #   then the loop will close and return control to the user.
    #   has_human_questions() in chat.py detects this and sets close_reason.
    human_questioners = [
        b for b in bids
        if b.turn_type == TURN_TYPE_QUESTION and b.target == "human"
    ]
    if human_questioners:
        speakers = [b.agent_id for b in human_questioners]
        logger.debug("facilitation_rule_2a_human_directed_questions", speakers=speakers)
        return speakers

    # Rule 2b: question bids with a specific agent target → route to that target.
    # Each target is allowed at most 2 Rule-2b routings per discussion to prevent
    # a loop where the same agent is repeatedly force-routed without ever letting
    # the conversation progress (e.g. other agents keep asking the same target
    # questions while a conveyance bidder is waiting to answer).
    MAX_RULE2B_ROUTES = 2
    for bid in bids:
        if bid.turn_type == TURN_TYPE_QUESTION and bid.target and bid.target in bid_map:
            if rule2b_counts is not None and rule2b_counts.get(bid.target, 0) >= MAX_RULE2B_ROUTES:
                logger.debug(
                    "facilitation_rule_2b_skipped_exhausted",
                    target=bid.target,
                    count=rule2b_counts.get(bid.target, 0),
                )
                continue
            if rule2b_counts is not None:
                rule2b_counts[bid.target] = rule2b_counts.get(bid.target, 0) + 1
            logger.debug(
                "facilitation_rule_2b_agent_question_routing",
                asker=bid.agent_id,
                target=bid.target,
                route_count=rule2b_counts.get(bid.target, 1) if rule2b_counts is not None else "?",
            )
            return [bid.target]

    # Rules 3–4: filter out passive bids
    active_bids = [b for b in bids if b.turn_type not in PASSIVE_TYPES]
    if not active_bids:
        logger.debug("facilitation_rule_4_no_active_bids_natural_closure")
        return []

    # Rule 3.5: exclude the last speaker from Rule 7 candidates to prevent
    # consecutive self-response. Exception: if they're the only remaining active
    # bidder, they may still speak (don't force artificial closure).
    eligible_bids = active_bids
    if last_speaker_id:
        others = [b for b in active_bids if b.agent_id != last_speaker_id]
        if others:
            eligible_bids = others
            logger.debug(
                "facilitation_rule_3_5_last_speaker_excluded",
                last_speaker_id=last_speaker_id,
                remaining=len(eligible_bids),
            )
        else:
            # Last speaker is the sole remaining active bidder.
            # If their bid is convergence, treat it as consensus closure — don't let
            # them repeat a convergence message they just delivered.
            last_speaker_bid = bid_map.get(last_speaker_id)
            if last_speaker_bid and last_speaker_bid.turn_type == TURN_TYPE_CONVERGENCE:
                logger.debug(
                    "facilitation_rule_3_5_sole_convergence_closure",
                    last_speaker_id=last_speaker_id,
                )
                return []
            logger.debug(
                "facilitation_rule_3_5_last_speaker_only_bidder",
                last_speaker_id=last_speaker_id,
            )

    # Rule 5: separate turn type pools; challenges precede convergences
    challenge_bids = [b for b in eligible_bids if b.turn_type == TURN_TYPE_CHALLENGE]
    convergence_bids = [b for b in eligible_bids if b.turn_type == TURN_TYPE_CONVERGENCE]
    other_bids = [b for b in eligible_bids if b.turn_type not in (TURN_TYPE_CHALLENGE, TURN_TYPE_CONVERGENCE)]

    # Priority ordering: challenges > others > convergences
    prioritized = challenge_bids + other_bids + convergence_bids

    # Rule 6: discount confidence for dominant speakers
    turn_variance = compute_turn_variance(recent_turn_counts)

    def adjusted_confidence(bid: BidResult) -> float:
        share = turn_variance.get(bid.agent_id, 0.0)
        if share > DOMINANCE_THRESHOLD:
            # Linear discount: excess share above threshold reduces confidence
            excess_ratio = (share - DOMINANCE_THRESHOLD) / DOMINANCE_THRESHOLD
            discount_factor = max(0.5, 1.0 - excess_ratio * 0.5)
            return bid.confidence * discount_factor
        return bid.confidence

    # Rule 7: select winner(s)
    sorted_bids = sorted(prioritized, key=adjusted_confidence, reverse=True)

    # Allow up to 2 concurrent challenges
    if len(challenge_bids) >= 2:
        top_challenges = sorted(challenge_bids, key=adjusted_confidence, reverse=True)[:2]
        selected = [b.agent_id for b in top_challenges]
        logger.debug(
            "facilitation_rule_7_concurrent_challenges",
            selected=selected,
            count=len(selected),
        )
        return selected

    winner = sorted_bids[0]
    logger.debug(
        "facilitation_rule_7_single_winner",
        agent_id=winner.agent_id,
        turn_type=winner.turn_type,
        confidence=round(winner.confidence, 2),
        adjusted=round(adjusted_confidence(winner), 2),
    )
    return [winner.agent_id]


def has_human_questions(bids: List[BidResult], selected_agent_ids: List[str]) -> bool:
    """
    Return True if any of the selected speakers bid a question directed at the human.

    Called in chat.py after select_speakers() and before generating responses.
    When True, the loop generates those agents' responses (the questions themselves)
    and then closes with reason "human_input_requested", returning control to the user.

    Args:
        bids: Full bid list from collect_bids()
        selected_agent_ids: Agents chosen to speak this turn

    Returns:
        True if control should return to the human after this turn
    """
    selected_set = set(selected_agent_ids)
    return any(
        b.agent_id in selected_set
        and b.turn_type == TURN_TYPE_QUESTION
        and b.target == "human"
        for b in bids
    )


def check_closure(bids: List[BidResult]) -> Tuple[bool, str]:
    """
    Determine whether distributed closure conditions are met.

    Closure conditions:
    - Natural:   All agents bid pass or backchannel
    - Consensus: All non-passing agents bid convergence; zero challenge bids

    Args:
        bids: BidResult list from collect_bids()

    Returns:
        (should_close: bool, reason: str)
    """
    if not bids:
        return True, "no_bidders"

    # Natural closure: everyone is done
    if all(b.turn_type in PASSIVE_TYPES for b in bids):
        logger.debug("closure_natural_all_pass")
        return True, "natural_all_pass"

    active_bids = [b for b in bids if b.turn_type not in PASSIVE_TYPES]
    challenge_bids = [b for b in active_bids if b.turn_type == TURN_TYPE_CHALLENGE]

    # Consensus closure: all active bids are convergence and no challenges remain
    if active_bids and not challenge_bids and all(b.turn_type == TURN_TYPE_CONVERGENCE for b in active_bids):
        logger.debug("closure_consensus_convergence")
        return True, "consensus_convergence"

    return False, ""
