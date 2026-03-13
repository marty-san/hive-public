"""Conversation models."""
from sqlalchemy import Column, String, Text, DateTime, Boolean, Integer, JSON, ForeignKey
from sqlalchemy.sql import func
from app.database import Base
import uuid


class Conversation(Base):
    """Conversation model representing a chat session."""

    __tablename__ = "conversations"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    status = Column(String, default="active")  # active, paused, completed, archived
    mode = Column(String, default="interactive")  # interactive, autonomous
    max_autonomous_turns = Column(Integer, default=20)
    requires_human_for_decisions = Column(Boolean, default=True)
    extra_data = Column(JSON, nullable=True)  # Renamed from metadata to avoid SQLAlchemy conflict

    def __repr__(self):
        return f"<Conversation(id={self.id}, title={self.title})>"


class ConversationParticipant(Base):
    """Many-to-many relationship between conversations and agents."""

    __tablename__ = "conversation_participants"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False)
    agent_id = Column(String, ForeignKey("agents.id"), nullable=False)
    joined_at = Column(DateTime, server_default=func.now())
    left_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)

    def __repr__(self):
        return f"<ConversationParticipant(conversation={self.conversation_id}, agent={self.agent_id})>"
