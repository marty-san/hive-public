"""Whiteboard models."""
from sqlalchemy import Column, String, Text, DateTime, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.sql import func
from app.database import Base
import uuid


class WhiteboardEntry(Base):
    """Materialized current state of a whiteboard entry."""

    __tablename__ = "whiteboard_entries"
    __table_args__ = (
        UniqueConstraint('conversation_id', 'key', name='uq_whiteboard_conv_key'),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False, index=True)
    key = Column(String(50), nullable=False)
    entry_type = Column(String(20), nullable=False)  # goal/decision/constraint/open_question/strategy
    value = Column(String(240), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    last_author_id = Column(String, nullable=True)
    last_author_type = Column(String(10), nullable=True)  # 'agent' or 'human'
    last_author_name = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<WhiteboardEntry(key={self.key}, type={self.entry_type})>"


class WhiteboardLog(Base):
    """Immutable event history for whiteboard changes."""

    __tablename__ = "whiteboard_log"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False, index=True)
    entry_key = Column(String(50), nullable=False)
    entry_type = Column(String(20), nullable=True)
    action = Column(String(10), nullable=False)  # 'set' or 'remove'
    author_id = Column(String, nullable=True)
    author_type = Column(String(10), nullable=True)  # 'agent' or 'human'
    author_name = Column(String, nullable=True)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    reason = Column(Text, nullable=True)
    message_id = Column(String, ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)

    def __repr__(self):
        return f"<WhiteboardLog(key={self.entry_key}, action={self.action})>"
