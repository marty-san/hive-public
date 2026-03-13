"""Facilitation utilities for bid-based agent orchestration."""
from typing import List


def extract_mentions(message_content: str, participant_agents: list) -> List[str]:
    """
    Extract @mentioned agent IDs from a message.

    Thin wrapper around parse_agent_mentions from chat.py, callable for both
    human and agent messages.
    """
    # Import inline to avoid circular imports at module load time
    from app.api.routes.chat import parse_agent_mentions
    return parse_agent_mentions(message_content, participant_agents)


def get_recent_mentions(messages: list, participant_agents: list, lookback: int = 3) -> List[str]:
    """
    Scan last N messages (any sender) for @mentions that haven't been answered yet.

    An @mention is considered "answered" if the mentioned agent has already
    sent a message after the one containing the mention — they responded to it.

    Args:
        messages: List of Message objects (any sender type)
        participant_agents: List of Agent objects in the conversation
        lookback: How many recent messages to scan (default 3)

    Returns:
        Ordered list of agent IDs mentioned (deduped, first-mention order),
        excluding agents that already responded after the mention.
    """
    if not messages or not participant_agents:
        return []

    recent = messages[-lookback:] if len(messages) >= lookback else messages

    mentioned_ids: List[str] = []
    for i, msg in enumerate(recent):
        mentions = extract_mentions(msg.content, participant_agents)
        for agent_id in mentions:
            if agent_id not in mentioned_ids:
                # Skip if the mentioned agent already responded after this message
                already_responded = any(
                    later_msg.sender_type == "agent" and later_msg.sender_id == agent_id
                    for later_msg in recent[i + 1:]
                )
                if not already_responded:
                    mentioned_ids.append(agent_id)

    return mentioned_ids


def compute_turn_variance(agent_turn_counts: dict) -> dict:
    """
    Compute each agent's fraction of total turns.

    Agents above 0.375 (3/8) of total turns are considered dominant and
    have their bid confidence discounted in select_speakers().

    Args:
        agent_turn_counts: Dict of {agent_id: turn_count}

    Returns:
        Dict of {agent_id: fraction_of_total_turns} (0.0–1.0)
    """
    total = sum(agent_turn_counts.values())
    if total == 0:
        return {agent_id: 0.0 for agent_id in agent_turn_counts}

    return {
        agent_id: count / total
        for agent_id, count in agent_turn_counts.items()
    }


def get_pending_human_questions(messages: list) -> List[str]:
    """
    Return content of agent questions directed at the human that have not yet been answered.

    A question is "pending" if it was bid with turn_type="question" and bid_target="human"
    and no human message has appeared after it.

    Args:
        messages: Ordered list of Message objects (oldest first)

    Returns:
        List of question content strings; empty if none pending
    """
    # Find the index of the last human message
    last_human_idx = -1
    for i, msg in enumerate(messages):
        if msg.sender_type == "human":
            last_human_idx = i

    # Collect agent questions directed at "human" that came after the last human message
    pending = []
    for msg in messages[last_human_idx + 1:]:
        if (
            msg.sender_type == "agent"
            and msg.extra_data
            and msg.extra_data.get("turn_type") == "question"
            and msg.extra_data.get("bid_target") == "human"
        ):
            pending.append(msg.content)

    return pending


def count_agent_turns_since_human(messages: list) -> int:
    """
    Count agent turns that have occurred since the last human message.

    When this count reaches 3+ and there are pending human questions, agents
    should proceed with explicit stated assumptions rather than re-asking.

    Args:
        messages: Ordered list of Message objects (oldest first)

    Returns:
        Integer count of agent turns since last human message
    """
    count = 0
    for msg in reversed(messages):
        if msg.sender_type == "human":
            break
        if msg.sender_type == "agent":
            count += 1
    return count
