"""In-memory interrupt flag registry for active conversations.

Replaces the hot-path DB query with a simple dict lookup.
The DB record (SessionState.interrupt_requested) is still written for audit.

On server restart, flags are lost — acceptable because in-flight conversations
are also lost at restart.
"""

_interrupt_flags: dict[str, bool] = {}


def request_interrupt(conv_id: str) -> None:
    """Set the interrupt flag for a conversation."""
    _interrupt_flags[conv_id] = True


def check_interrupt(conv_id: str) -> bool:
    """Return True if an interrupt has been requested for this conversation."""
    return _interrupt_flags.get(conv_id, False)


def clear_interrupt(conv_id: str) -> None:
    """Clear the interrupt flag after the discussion loop has exited."""
    _interrupt_flags.pop(conv_id, None)
