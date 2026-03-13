"""In-memory state for proposal voting.

Used to coordinate human vote signals between the proposal service (running
inside a request handler) and the vote endpoint (a separate HTTP request).
Uses asyncio.Event so the proposal service can await the human vote without
blocking the event loop.
"""
import asyncio
from typing import Dict, Optional

_vote_events: Dict[str, asyncio.Event] = {}
_human_votes: Dict[str, Optional[str]] = {}  # "approve" | "reject" | None


def create_vote_event(proposal_id: str) -> asyncio.Event:
    """Create a new asyncio.Event for a pending human vote."""
    event = asyncio.Event()
    _vote_events[proposal_id] = event
    _human_votes[proposal_id] = None
    return event


def submit_human_vote(proposal_id: str, vote: str) -> bool:
    """Set the human vote and signal the waiting proposal service.

    Returns True if a pending event was found, False if the proposal
    is no longer waiting (timed out or already resolved).
    """
    if proposal_id not in _vote_events:
        return False
    _human_votes[proposal_id] = vote
    _vote_events[proposal_id].set()
    return True


def get_human_vote(proposal_id: str) -> Optional[str]:
    return _human_votes.get(proposal_id)


def clear_proposal(proposal_id: str) -> None:
    _vote_events.pop(proposal_id, None)
    _human_votes.pop(proposal_id, None)
