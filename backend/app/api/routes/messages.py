"""Message management routes."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from app.database import get_db
from app.models import Message, Conversation, Agent
from app.schemas import MessageCreate, MessageResponse
from app.services.websocket_manager import ws_manager

router = APIRouter()


@router.get("/{conversation_id}/messages", response_model=List[MessageResponse])
async def get_messages(
    conversation_id: str,
    limit: int = 10000,
    offset: int = 0,
    db: AsyncSession = Depends(get_db)
):
    """Get messages for a conversation."""
    # Verify conversation exists
    conv_result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = conv_result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Get messages
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .limit(limit)
        .offset(offset)
    )
    messages = result.scalars().all()

    # Enrich with sender names
    responses = []
    for msg in messages:
        sender_name = None
        if msg.sender_type == "agent" and msg.sender_id:
            agent_result = await db.execute(
                select(Agent.name).where(Agent.id == msg.sender_id)
            )
            sender_name = agent_result.scalar_one_or_none()
        elif msg.sender_type == "human":
            sender_name = "You"

        responses.append(
            MessageResponse(
                **msg.__dict__,
                sender_name=sender_name
            )
        )

    return responses


@router.post("/{conversation_id}/messages", response_model=MessageResponse, status_code=201)
async def create_message(
    conversation_id: str,
    message_data: MessageCreate,
    db: AsyncSession = Depends(get_db)
):
    """Create a new message (human-sent)."""
    # Verify conversation exists
    conv_result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = conv_result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Ensure conversation_id matches
    if message_data.conversation_id != conversation_id:
        raise HTTPException(
            status_code=400,
            detail="Conversation ID mismatch"
        )

    # Create message
    message = Message(**message_data.model_dump(exclude={"attachments"}))
    db.add(message)
    await db.commit()
    await db.refresh(message)

    # Build response
    response = MessageResponse(
        **message.__dict__,
        sender_name="You"
    )

    # Broadcast via WebSocket
    await ws_manager.send_message_event(
        conversation_id,
        response.model_dump(mode='json')
    )

    return response


@router.get("/{conversation_id}/messages/{message_id}", response_model=MessageResponse)
async def get_message(
    conversation_id: str,
    message_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get a specific message."""
    result = await db.execute(
        select(Message)
        .where(Message.id == message_id)
        .where(Message.conversation_id == conversation_id)
    )
    message = result.scalar_one_or_none()

    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    # Get sender name
    sender_name = None
    if message.sender_type == "agent" and message.sender_id:
        agent_result = await db.execute(
            select(Agent.name).where(Agent.id == message.sender_id)
        )
        sender_name = agent_result.scalar_one_or_none()
    elif message.sender_type == "human":
        sender_name = "You"

    return MessageResponse(
        **message.__dict__,
        sender_name=sender_name
    )
