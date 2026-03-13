"""Memory models for agents."""
from sqlalchemy import Column, String, Text, DateTime, Float, Integer, SmallInteger, JSON, ForeignKey
from sqlalchemy.sql import func
from app.database import Base
import uuid


class AgentWorkingMemory(Base):
    """Working memory for agents - small, always-loaded context."""

    __tablename__ = "agent_working_memory"

    agent_id = Column(String, ForeignKey("agents.id"), primary_key=True)
    current_goals = Column(JSON, nullable=True)  # Array of active goals
    active_constraints = Column(JSON, nullable=True)  # Current rules/limitations
    conversation_contexts = Column(JSON, nullable=True)  # Map of conversation_id -> context
    last_updated = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<AgentWorkingMemory(agent_id={self.agent_id})>"


class AgentEpisodicMemory(Base):
    """Episodic memory for agents - events and facts from conversations."""

    __tablename__ = "agent_episodic_memories"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_id = Column(String, ForeignKey("agents.id"), nullable=False, index=True)
    memory_type = Column(String, nullable=False)  # 'fact_state', 'observation_preference', 'event', 'decision', 'rejection', 'reflection_summary'
    content = Column(Text, nullable=False)
    source_message_id = Column(String, ForeignKey("messages.id"), nullable=True)
    source_conversation_id = Column(String, ForeignKey("conversations.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    confidence = Column(Float, default=1.0)
    access_count = Column(Integer, default=0)
    last_accessed_at = Column(DateTime, nullable=True)
    embedding = Column(JSON, nullable=True)  # Vector embedding for semantic search (1536 dimensions)
    extra_data = Column(JSON, nullable=True)  # Structured attributes: attribute_key, keywords, tags
    # Bitemporal columns
    valid_from = Column(DateTime, nullable=True)      # When this fact became true in the world
    valid_until = Column(DateTime, nullable=True)     # When this fact stopped being true; NULL = currently active
    asserted_at = Column(DateTime, nullable=True)     # When the system began treating this as active knowledge
    asserted_until = Column(DateTime, nullable=True)  # When the system retired this from active knowledge
    superseded_by = Column(String, nullable=True)     # ID of the memory that replaced this one (mutable types only)
    occurred_at = Column(DateTime, nullable=True)     # For events: when it actually happened (may differ from created_at)
    importance = Column(SmallInteger, nullable=True)  # LLM-assigned 1-10 importance at write time

    def __repr__(self):
        return f"<AgentEpisodicMemory(id={self.id}, type={self.memory_type})>"


class AgentSemanticMemory(Base):
    """Semantic memory for agents - consolidated knowledge."""

    __tablename__ = "agent_semantic_memories"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_id = Column(String, ForeignKey("agents.id"), nullable=False, index=True)
    category = Column(String, nullable=False, index=True)  # 'preference', 'rule', 'concept', 'relationship'
    key = Column(String, nullable=False)
    value = Column(Text, nullable=False)
    source_count = Column(Integer, default=1)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    confidence = Column(Float, default=1.0)
    extra_data = Column(JSON, nullable=True)  # Renamed from metadata to avoid SQLAlchemy conflict

    def __repr__(self):
        return f"<AgentSemanticMemory(id={self.id}, category={self.category})>"
