"""Whiteboard service for managing shared conversation state."""
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import structlog

from app.models.whiteboard import WhiteboardEntry, WhiteboardLog

logger = structlog.get_logger()

MAX_VALUE_LENGTH = 240


async def get_current_state(
    conversation_id: str,
    db: AsyncSession,
) -> List[WhiteboardEntry]:
    """Return all active whiteboard entries for a conversation, newest first."""
    result = await db.execute(
        select(WhiteboardEntry)
        .where(
            WhiteboardEntry.conversation_id == conversation_id,
            WhiteboardEntry.is_active == True,  # noqa: E712
        )
        .order_by(WhiteboardEntry.updated_at.desc())
    )
    return list(result.scalars().all())


async def set_entry(
    conversation_id: str,
    key: str,
    entry_type: str,
    value: str,
    reason: str,
    author_id: Optional[str],
    author_type: str,
    author_name: str,
    db: AsyncSession,
    message_id: Optional[str] = None,
) -> WhiteboardLog:
    """
    Upsert a whiteboard entry and append a log record.

    Raises ValueError if len(value) > MAX_VALUE_LENGTH.
    """
    if len(value) > MAX_VALUE_LENGTH:
        raise ValueError(f"Whiteboard value exceeds {MAX_VALUE_LENGTH} characters")

    # Look up existing entry
    entry_result = await db.execute(
        select(WhiteboardEntry).where(
            WhiteboardEntry.conversation_id == conversation_id,
            WhiteboardEntry.key == key,
        )
    )
    entry = entry_result.scalar_one_or_none()

    old_value = None
    if entry:
        old_value = entry.value if entry.is_active else None
        entry.entry_type = entry_type
        entry.value = value
        entry.is_active = True
        entry.last_author_id = author_id
        entry.last_author_type = author_type
        entry.last_author_name = author_name
    else:
        import uuid
        entry = WhiteboardEntry(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            key=key,
            entry_type=entry_type,
            value=value,
            is_active=True,
            last_author_id=author_id,
            last_author_type=author_type,
            last_author_name=author_name,
        )
        db.add(entry)

    log = WhiteboardLog(
        conversation_id=conversation_id,
        entry_key=key,
        entry_type=entry_type,
        action="set",
        author_id=author_id,
        author_type=author_type,
        author_name=author_name,
        old_value=old_value,
        new_value=value,
        reason=reason,
        message_id=message_id,
    )
    db.add(log)
    await db.commit()

    logger.info(
        "whiteboard_entry_set",
        conversation_id=conversation_id,
        key=key,
        author_name=author_name,
    )
    return log


async def remove_entry(
    conversation_id: str,
    key: str,
    reason: str,
    author_id: Optional[str],
    author_type: str,
    author_name: str,
    db: AsyncSession,
    message_id: Optional[str] = None,
) -> Optional[WhiteboardLog]:
    """
    Soft-delete a whiteboard entry and append a log record.

    Returns None if the entry doesn't exist or is already inactive.
    """
    entry_result = await db.execute(
        select(WhiteboardEntry).where(
            WhiteboardEntry.conversation_id == conversation_id,
            WhiteboardEntry.key == key,
            WhiteboardEntry.is_active == True,  # noqa: E712
        )
    )
    entry = entry_result.scalar_one_or_none()
    if not entry:
        return None

    old_value = entry.value
    entry.is_active = False
    entry.last_author_id = author_id
    entry.last_author_type = author_type
    entry.last_author_name = author_name

    log = WhiteboardLog(
        conversation_id=conversation_id,
        entry_key=key,
        entry_type=entry.entry_type,
        action="remove",
        author_id=author_id,
        author_type=author_type,
        author_name=author_name,
        old_value=old_value,
        new_value=None,
        reason=reason,
        message_id=message_id,
    )
    db.add(log)
    await db.commit()

    logger.info(
        "whiteboard_entry_removed",
        conversation_id=conversation_id,
        key=key,
        author_name=author_name,
    )
    return log


async def get_history(
    conversation_id: str,
    db: AsyncSession,
    key: Optional[str] = None,
) -> List[WhiteboardLog]:
    """Return whiteboard log entries, optionally filtered by key."""
    query = select(WhiteboardLog).where(
        WhiteboardLog.conversation_id == conversation_id
    )
    if key:
        query = query.where(WhiteboardLog.entry_key == key)
    query = query.order_by(WhiteboardLog.created_at.asc())
    result = await db.execute(query)
    return list(result.scalars().all())
