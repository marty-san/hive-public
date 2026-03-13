"""Session state models."""
from sqlalchemy import Column, String, DateTime, Boolean, Integer, JSON, ForeignKey
from sqlalchemy.sql import func
from app.database import Base


class SessionState(Base):
    """Session state for managing autonomous conversations."""

    __tablename__ = "session_states"

    conversation_id = Column(String, ForeignKey("conversations.id"), primary_key=True)
    is_autonomous = Column(Boolean, default=False)
    turn_count = Column(Integer, default=0)
    start_time = Column(DateTime, nullable=True)
    last_activity = Column(DateTime, nullable=True)
    paused_at = Column(DateTime, nullable=True)
    awaiting_human_decision = Column(Boolean, default=False)
    decision_context = Column(JSON, nullable=True)
    interrupt_requested = Column(Boolean, default=False)
    human_votes_on_proposals = Column(Boolean, default=False)

    def __repr__(self):
        return f"<SessionState(conversation_id={self.conversation_id}, is_autonomous={self.is_autonomous})>"
