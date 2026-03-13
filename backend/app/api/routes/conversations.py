"""Conversation management routes."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from pydantic import BaseModel as _BaseModel

from app.database import get_db
from app.models import Conversation, ConversationParticipant, Agent, Message
from app.schemas import (
    ConversationCreate,
    ConversationUpdate,
    ConversationResponse,
    AddParticipantRequest
)
from fastapi.responses import JSONResponse
import json

router = APIRouter()


@router.post("", response_model=ConversationResponse, status_code=201)
async def create_conversation(
    conversation_data: ConversationCreate,
    db: AsyncSession = Depends(get_db)
):
    """Create a new conversation."""
    # Create conversation
    conv_dict = conversation_data.model_dump(exclude={"initial_participants"})
    conversation = Conversation(**conv_dict)
    db.add(conversation)
    await db.flush()

    # Add initial participants
    participant_ids = []
    if conversation_data.initial_participants:
        for agent_id in conversation_data.initial_participants:
            # Verify agent exists
            result = await db.execute(
                select(Agent).where(Agent.id == agent_id)
            )
            agent = result.scalar_one_or_none()
            if not agent:
                raise HTTPException(
                    status_code=404,
                    detail=f"Agent {agent_id} not found"
                )

            # Add participant
            participant = ConversationParticipant(
                conversation_id=conversation.id,
                agent_id=agent_id
            )
            db.add(participant)
            participant_ids.append(agent_id)

    await db.commit()
    await db.refresh(conversation)

    # Build response
    response = ConversationResponse(
        **conversation.__dict__,
        participant_ids=participant_ids
    )
    return response


@router.get("", response_model=List[ConversationResponse])
async def list_conversations(
    status: str = None,
    db: AsyncSession = Depends(get_db)
):
    """List all conversations."""
    query = select(Conversation).order_by(Conversation.updated_at.desc())

    if status:
        query = query.where(Conversation.status == status)
    else:
        # By default, exclude archived conversations
        query = query.where(Conversation.status != "archived")

    result = await db.execute(query)
    conversations = result.scalars().all()

    # Get participants for each conversation
    responses = []
    for conv in conversations:
        part_result = await db.execute(
            select(ConversationParticipant.agent_id)
            .where(ConversationParticipant.conversation_id == conv.id)
            .where(ConversationParticipant.is_active == True)
        )
        participant_ids = [row[0] for row in part_result.all()]

        responses.append(
            ConversationResponse(
                **conv.__dict__,
                participant_ids=participant_ids
            )
        )

    return responses


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get a specific conversation."""
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Get participants
    part_result = await db.execute(
        select(ConversationParticipant.agent_id)
        .where(ConversationParticipant.conversation_id == conversation_id)
        .where(ConversationParticipant.is_active == True)
    )
    participant_ids = [row[0] for row in part_result.all()]

    return ConversationResponse(
        **conversation.__dict__,
        participant_ids=participant_ids
    )


@router.put("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: str,
    conversation_data: ConversationUpdate,
    db: AsyncSession = Depends(get_db)
):
    """Update a conversation."""
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Update fields
    update_data = conversation_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(conversation, field, value)

    await db.commit()
    await db.refresh(conversation)

    # Get participants
    part_result = await db.execute(
        select(ConversationParticipant.agent_id)
        .where(ConversationParticipant.conversation_id == conversation_id)
        .where(ConversationParticipant.is_active == True)
    )
    participant_ids = [row[0] for row in part_result.all()]

    return ConversationResponse(
        **conversation.__dict__,
        participant_ids=participant_ids
    )


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: str,
    delete_memories: bool = False,
    db: AsyncSession = Depends(get_db)
):
    """
    Delete a conversation and all associated records.

    Args:
        conversation_id: ID of conversation to delete
        delete_memories: If True, permanently delete all episodic memories from this conversation.
                        If False (default), memories are preserved but conversation reference is cleared.
    """
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Import here to avoid circular imports
    from app.models import (
        Message, MessageAttachment, ConversationParticipant,
        SessionState, AgentEpisodicMemory, ToolUsageLog, AgentWorkingMemory
    )

    # 1. Delete message attachments (via messages)
    messages_result = await db.execute(
        select(Message.id).where(Message.conversation_id == conversation_id)
    )
    message_ids = [row[0] for row in messages_result.all()]

    if message_ids:
        await db.execute(
            MessageAttachment.__table__.delete().where(
                MessageAttachment.message_id.in_(message_ids)
            )
        )

    # 2. Delete messages
    await db.execute(
        Message.__table__.delete().where(Message.conversation_id == conversation_id)
    )

    # 3. Delete conversation participants
    await db.execute(
        ConversationParticipant.__table__.delete().where(
            ConversationParticipant.conversation_id == conversation_id
        )
    )

    # 4. Delete session state
    await db.execute(
        SessionState.__table__.delete().where(
            SessionState.conversation_id == conversation_id
        )
    )

    # 5. Handle episodic memories based on delete_memories flag
    if delete_memories:
        # DEEP DELETE: Permanently remove all episodic memories from this conversation
        await db.execute(
            AgentEpisodicMemory.__table__.delete().where(
                AgentEpisodicMemory.source_conversation_id == conversation_id
            )
        )
    else:
        # SOFT DELETE: Clear source_conversation_id reference but keep the memory
        # Memories persist even when their source conversation is deleted
        memories_result = await db.execute(
            select(AgentEpisodicMemory).where(
                AgentEpisodicMemory.source_conversation_id == conversation_id
            )
        )
        memories = memories_result.scalars().all()
        for memory in memories:
            memory.source_conversation_id = None  # Clear the reference but keep the memory
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(memory, "source_conversation_id")

    # 6. Delete tool usage logs
    await db.execute(
        ToolUsageLog.__table__.delete().where(
            ToolUsageLog.conversation_id == conversation_id
        )
    )

    # 7. Clean up working memory conversation contexts (JSON field)
    # Get all working memories that might reference this conversation
    working_memories_result = await db.execute(select(AgentWorkingMemory))
    working_memories = working_memories_result.scalars().all()

    for wm in working_memories:
        if wm.conversation_contexts and conversation_id in wm.conversation_contexts:
            wm.conversation_contexts.pop(conversation_id)
            # Mark as modified for SQLAlchemy to detect the change
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(wm, "conversation_contexts")

    # 8. Finally, delete the conversation itself
    await db.delete(conversation)
    await db.commit()

    return None


@router.post("/{conversation_id}/participants", status_code=201)
async def add_participant(
    conversation_id: str,
    request: AddParticipantRequest,
    db: AsyncSession = Depends(get_db)
):
    """Add an agent to a conversation."""
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

    # Check if already a participant
    part_result = await db.execute(
        select(ConversationParticipant)
        .where(ConversationParticipant.conversation_id == conversation_id)
        .where(ConversationParticipant.agent_id == request.agent_id)
        .where(ConversationParticipant.is_active == True)
    )
    existing = part_result.scalar_one_or_none()

    if existing:
        raise HTTPException(status_code=400, detail="Agent is already a participant")

    # Add participant
    participant = ConversationParticipant(
        conversation_id=conversation_id,
        agent_id=request.agent_id
    )
    db.add(participant)

    # Create system message
    system_message = Message(
        conversation_id=conversation_id,
        sender_type="system",
        content=f"{agent.name} joined the conversation"
    )
    db.add(system_message)
    await db.commit()
    await db.refresh(system_message)

    # Broadcast system message via WebSocket
    from app.schemas import MessageResponse
    from app.services.websocket_manager import ws_manager
    await ws_manager.send_message_event(
        conversation_id,
        MessageResponse(
            **system_message.__dict__,
            sender_name=None
        ).model_dump(mode='json')
    )

    return {"message": "Participant added successfully"}


@router.delete("/{conversation_id}/participants/{agent_id}", status_code=204)
async def remove_participant(
    conversation_id: str,
    agent_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Remove an agent from a conversation."""
    result = await db.execute(
        select(ConversationParticipant)
        .where(ConversationParticipant.conversation_id == conversation_id)
        .where(ConversationParticipant.agent_id == agent_id)
        .where(ConversationParticipant.is_active == True)
    )
    participant = result.scalar_one_or_none()

    if not participant:
        raise HTTPException(status_code=404, detail="Participant not found")

    # Get agent info for system message
    agent_result = await db.execute(
        select(Agent).where(Agent.id == agent_id)
    )
    agent = agent_result.scalar_one_or_none()

    participant.is_active = False

    # Create system message
    if agent:
        system_message = Message(
            conversation_id=conversation_id,
            sender_type="system",
            content=f"{agent.name} left the conversation"
        )
        db.add(system_message)
        await db.commit()
        await db.refresh(system_message)

        # Broadcast system message via WebSocket
        from app.schemas import MessageResponse
        from app.services.websocket_manager import ws_manager
        await ws_manager.send_message_event(
            conversation_id,
            MessageResponse(
                **system_message.__dict__,
                sender_name=None
            ).model_dump(mode='json')
        )
    else:
        await db.commit()

    return None


@router.get("/{conversation_id}/export")
async def export_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Export conversation as JSON."""
    # Get conversation
    conv_result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = conv_result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Get messages
    messages_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )
    messages = messages_result.scalars().all()

    # Get participants
    part_result = await db.execute(
        select(Agent)
        .join(ConversationParticipant, Agent.id == ConversationParticipant.agent_id)
        .where(ConversationParticipant.conversation_id == conversation_id)
    )
    agents = {agent.id: agent.name for agent in part_result.scalars().all()}

    # Build export data
    export_data = {
        "conversation": {
            "id": conversation.id,
            "title": conversation.title,
            "created_at": conversation.created_at.isoformat(),
            "updated_at": conversation.updated_at.isoformat(),
        },
        "participants": agents,
        "messages": [
            {
                "id": msg.id,
                "sender_type": msg.sender_type,
                "sender_name": agents.get(msg.sender_id, "You") if msg.sender_type == "agent" else "You",
                "content": msg.content,
                "created_at": msg.created_at.isoformat()
            }
            for msg in messages
        ]
    }

    return JSONResponse(content=export_data)


class ProposalVoteRequest(_BaseModel):
    vote: str  # "approve" | "reject"


@router.post("/{conversation_id}/proposals/{proposal_id}/vote")
async def vote_on_proposal(
    conversation_id: str,
    proposal_id: str,
    request: ProposalVoteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Submit the human vote on an active proposal."""
    if request.vote not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="vote must be 'approve' or 'reject'")

    from app.services import proposal_state
    found = proposal_state.submit_human_vote(proposal_id, request.vote)
    if not found:
        raise HTTPException(status_code=404, detail="Proposal not found or already resolved")

    return {"ok": True}


class ConversationSettingsRequest(_BaseModel):
    human_votes_on_proposals: bool


@router.put("/{conversation_id}/settings")
async def update_conversation_settings(
    conversation_id: str,
    request: ConversationSettingsRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update conversation-level settings (human_votes_on_proposals, etc.)."""
    # Verify conversation exists
    conv_result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    if not conv_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Conversation not found")

    from app.models import SessionState as _SessionState
    session_result = await db.execute(
        select(_SessionState).where(_SessionState.conversation_id == conversation_id)
    )
    session = session_result.scalar_one_or_none()
    if not session:
        session = _SessionState(conversation_id=conversation_id)
        db.add(session)

    session.human_votes_on_proposals = request.human_votes_on_proposals
    await db.commit()

    return {"human_votes_on_proposals": session.human_votes_on_proposals}


@router.get("/{conversation_id}/settings")
async def get_conversation_settings(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get conversation-level settings."""
    from app.models import SessionState as _SessionState
    session_result = await db.execute(
        select(_SessionState).where(_SessionState.conversation_id == conversation_id)
    )
    session = session_result.scalar_one_or_none()
    return {
        "human_votes_on_proposals": bool(session.human_votes_on_proposals) if session else False,
    }
