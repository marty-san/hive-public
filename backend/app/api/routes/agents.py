"""Agent management routes."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
import structlog

from app.database import get_db
from app.models import Agent
from app.schemas import AgentCreate, AgentUpdate, AgentResponse
from app.services.embedding_service import embedding_service

logger = structlog.get_logger()
router = APIRouter()


async def generate_agent_embedding(expertise_domain: str, system_prompt: str) -> List[float]:
    """
    Generate embedding for an agent by combining expertise and system prompt.

    Args:
        expertise_domain: Agent's expertise domain
        system_prompt: Agent's system prompt

    Returns:
        Embedding vector (1536 dimensions)
    """
    # Combine expertise and system prompt with clear structure
    combined_text = f"""Agent Expertise: {expertise_domain}

Agent Role and Behavior: {system_prompt}"""

    try:
        embedding = await embedding_service.generate_embedding(combined_text)
        logger.info(
            "agent_embedding_generated",
            expertise_length=len(expertise_domain),
            prompt_length=len(system_prompt),
            embedding_dimensions=len(embedding)
        )
        return embedding
    except Exception as e:
        logger.error("agent_embedding_generation_failed", error=str(e))
        # Return empty list on failure - agent can still function with keyword matching
        return []


@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(
    agent_data: AgentCreate,
    db: AsyncSession = Depends(get_db)
):
    """Create a new agent."""
    # Check if agent with same name exists
    result = await db.execute(
        select(Agent).where(Agent.name == agent_data.name)
    )
    existing_agent = result.scalar_one_or_none()

    if existing_agent:
        raise HTTPException(status_code=400, detail="Agent with this name already exists")

    # Generate embedding for the agent
    embedding = await generate_agent_embedding(
        expertise_domain=agent_data.expertise_domain,
        system_prompt=agent_data.system_prompt
    )

    # Create new agent with embedding
    agent_dict = agent_data.model_dump()
    agent_dict['embedding'] = embedding
    agent = Agent(**agent_dict)
    db.add(agent)
    await db.commit()
    await db.refresh(agent)

    logger.info("agent_created_with_embedding", agent_id=agent.id, agent_name=agent.name)

    return agent


@router.get("", response_model=List[AgentResponse])
async def list_agents(
    db: AsyncSession = Depends(get_db)
):
    """List all agents."""
    result = await db.execute(select(Agent).order_by(Agent.created_at.desc()))
    agents = result.scalars().all()
    return agents


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get a specific agent."""
    result = await db.execute(
        select(Agent).where(Agent.id == agent_id)
    )
    agent = result.scalar_one_or_none()

    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    return agent


@router.put("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: str,
    agent_data: AgentUpdate,
    db: AsyncSession = Depends(get_db)
):
    """Update an agent."""
    result = await db.execute(
        select(Agent).where(Agent.id == agent_id)
    )
    agent = result.scalar_one_or_none()

    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Update fields
    update_data = agent_data.model_dump(exclude_unset=True)

    # Check if expertise_domain or system_prompt changed - need to regenerate embedding
    needs_embedding_update = (
        'expertise_domain' in update_data or
        'system_prompt' in update_data
    )

    for field, value in update_data.items():
        setattr(agent, field, value)

    # Regenerate embedding if needed
    if needs_embedding_update:
        embedding = await generate_agent_embedding(
            expertise_domain=agent.expertise_domain,
            system_prompt=agent.system_prompt
        )
        agent.embedding = embedding
        logger.info("agent_embedding_regenerated", agent_id=agent.id, agent_name=agent.name)

    await db.commit()
    await db.refresh(agent)

    return agent


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Delete an agent and all associated records."""
    result = await db.execute(
        select(Agent).where(Agent.id == agent_id)
    )
    agent = result.scalar_one_or_none()

    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Import here to avoid circular imports
    from app.models import (
        Message, MessageAttachment, ConversationParticipant,
        AgentWorkingMemory, AgentEpisodicMemory, AgentSemanticMemory,
        ToolUsageLog
    )

    # 1. Delete message attachments (via agent's messages)
    messages_result = await db.execute(
        select(Message.id).where(
            Message.sender_id == agent_id,
            Message.sender_type == "agent"
        )
    )
    message_ids = [row[0] for row in messages_result.all()]

    if message_ids:
        await db.execute(
            MessageAttachment.__table__.delete().where(
                MessageAttachment.message_id.in_(message_ids)
            )
        )

    # 2. Delete messages sent by this agent
    await db.execute(
        Message.__table__.delete().where(
            Message.sender_id == agent_id,
            Message.sender_type == "agent"
        )
    )

    # 3. Delete conversation participants
    await db.execute(
        ConversationParticipant.__table__.delete().where(
            ConversationParticipant.agent_id == agent_id
        )
    )

    # 4. Delete agent working memory
    await db.execute(
        AgentWorkingMemory.__table__.delete().where(
            AgentWorkingMemory.agent_id == agent_id
        )
    )

    # 5. Delete agent episodic memories
    await db.execute(
        AgentEpisodicMemory.__table__.delete().where(
            AgentEpisodicMemory.agent_id == agent_id
        )
    )

    # 6. Delete agent semantic memories
    await db.execute(
        AgentSemanticMemory.__table__.delete().where(
            AgentSemanticMemory.agent_id == agent_id
        )
    )

    # 7. Delete tool usage logs
    await db.execute(
        ToolUsageLog.__table__.delete().where(
            ToolUsageLog.agent_id == agent_id
        )
    )

    # 8. Finally, delete the agent itself
    await db.delete(agent)
    await db.commit()

    logger.info("agent_deleted_with_cascade", agent_id=agent_id, agent_name=agent.name)

    return None
