"""Whiteboard API routes."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from app.database import get_db
from app.models import Message
from app.models.whiteboard import WhiteboardEntry, WhiteboardLog
from app.services import whiteboard_service
from app.services.websocket_manager import ws_manager
from app.schemas.message import MessageResponse
import structlog

logger = structlog.get_logger()
router = APIRouter()


class WhiteboardSetBody(BaseModel):
    entry_type: str
    value: str
    reason: str


class WhiteboardRemoveBody(BaseModel):
    reason: str


def _entry_to_dict(entry: WhiteboardEntry) -> dict:
    return {
        "id": entry.id,
        "key": entry.key,
        "entry_type": entry.entry_type,
        "value": entry.value,
        "last_author_name": entry.last_author_name,
        "last_author_type": entry.last_author_type,
        "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


def _log_to_dict(log: WhiteboardLog) -> dict:
    return {
        "id": log.id,
        "entry_key": log.entry_key,
        "entry_type": log.entry_type,
        "action": log.action,
        "author_name": log.author_name,
        "author_type": log.author_type,
        "old_value": log.old_value,
        "new_value": log.new_value,
        "reason": log.reason,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


async def _broadcast_and_save(
    conversation_id: str,
    entries: List[WhiteboardEntry],
    change: dict,
    db: AsyncSession,
) -> None:
    """Broadcast whiteboard.updated WS event and save a system message."""
    # 1. Broadcast whiteboard state
    await ws_manager.send_whiteboard_event(
        conversation_id=conversation_id,
        entries=[_entry_to_dict(e) for e in entries],
        change=change,
    )

    # 2. Build system message text
    action = change.get("action", "updated")
    key = change.get("key", "")
    entry_type = change.get("entry_type", "")
    value = change.get("value", "")
    author = change.get("author_name", "Human")

    if action == "set":
        content = f'{author} updated whiteboard [{entry_type}] {key}: "{value}"'
    else:
        content = f'{author} removed whiteboard entry {key}'

    # 3. Save system message
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

    # 4. Broadcast system message
    await ws_manager.send_message_event(
        conversation_id,
        MessageResponse(**msg.__dict__, sender_name=None).model_dump(mode="json"),
    )


@router.get("/{conversation_id}/whiteboard")
async def get_whiteboard(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get current whiteboard state for a conversation."""
    entries = await whiteboard_service.get_current_state(conversation_id, db)
    return [_entry_to_dict(e) for e in entries]


@router.get("/{conversation_id}/whiteboard/history")
async def get_whiteboard_history(
    conversation_id: str,
    key: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Get whiteboard change history, optionally filtered by key."""
    logs = await whiteboard_service.get_history(conversation_id, db, key=key)
    return [_log_to_dict(l) for l in logs]


@router.put("/{conversation_id}/whiteboard/{key}")
async def set_whiteboard_entry(
    conversation_id: str,
    key: str,
    body: WhiteboardSetBody,
    db: AsyncSession = Depends(get_db),
):
    """Set (create or update) a whiteboard entry as a human."""
    try:
        await whiteboard_service.set_entry(
            conversation_id=conversation_id,
            key=key,
            entry_type=body.entry_type,
            value=body.value,
            reason=body.reason,
            author_id=None,
            author_type="human",
            author_name="Human",
            db=db,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    entries = await whiteboard_service.get_current_state(conversation_id, db)
    change = {
        "action": "set",
        "key": key,
        "entry_type": body.entry_type,
        "value": body.value,
        "author_name": "Human",
        "author_type": "human",
        "reason": body.reason,
    }
    await _broadcast_and_save(conversation_id, entries, change, db)
    return [_entry_to_dict(e) for e in entries]


@router.delete("/{conversation_id}/whiteboard/{key}")
async def remove_whiteboard_entry(
    conversation_id: str,
    key: str,
    body: WhiteboardRemoveBody,
    db: AsyncSession = Depends(get_db),
):
    """Remove a whiteboard entry as a human."""
    log = await whiteboard_service.remove_entry(
        conversation_id=conversation_id,
        key=key,
        reason=body.reason,
        author_id=None,
        author_type="human",
        author_name="Human",
        db=db,
    )
    if log is None:
        raise HTTPException(status_code=404, detail=f"Whiteboard entry '{key}' not found")

    entries = await whiteboard_service.get_current_state(conversation_id, db)
    change = {
        "action": "remove",
        "key": key,
        "author_name": "Human",
        "author_type": "human",
        "reason": body.reason,
    }
    await _broadcast_and_save(conversation_id, entries, change, db)
    return [_entry_to_dict(e) for e in entries]
