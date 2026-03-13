"""Message models."""
from sqlalchemy import Column, String, Text, DateTime, Boolean, JSON, ForeignKey
from sqlalchemy.sql import func
from app.database import Base
import uuid


class TurnType:
    """Turn type constants for bid metadata stored in message extra_data.

    extra_data shape when set by the bid system:
    {
        "turn_type":      str,        # one of the constants below
        "bid_confidence": float,      # 0.0–1.0
        "bid_target":     str | None, # agent_id of the targeted agent
        "bid_preview":    str | None, # one-sentence preview from the bid
    }
    """
    CONVEYANCE  = "conveyance"   # New domain information
    CHALLENGE   = "challenge"    # Contradiction or substantially different view
    QUESTION    = "question"     # Directed inquiry using @mention to target
    CONVERGENCE = "convergence"  # Synthesis, moving toward closure
    PASS        = "pass"         # Actively choosing not to contribute
    BACKCHANNEL = "backchannel"  # Acknowledgment, no floor claim
    PROPOSE_ADDITION = "propose_addition"  # Propose adding a new agent
    PROPOSE_REMOVAL  = "propose_removal"   # Propose removing an existing agent


class Message(Base):
    """Message model representing a chat message."""

    __tablename__ = "messages"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False, index=True)
    sender_type = Column(String, nullable=False)  # 'human', 'agent', 'system'
    sender_id = Column(String, ForeignKey("agents.id"), nullable=True)  # NULL for human
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    parent_message_id = Column(String, ForeignKey("messages.id"), nullable=True)
    requires_human_decision = Column(Boolean, default=False)
    decision_resolved = Column(Boolean, default=False)
    extra_data = Column(JSON, nullable=True)  # Renamed from metadata to avoid SQLAlchemy conflict

    def __repr__(self):
        return f"<Message(id={self.id}, sender_type={self.sender_type})>"


class MessageAttachment(Base):
    """Attachment model for images and files in messages."""

    __tablename__ = "message_attachments"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    message_id = Column(String, ForeignKey("messages.id"), nullable=False)
    attachment_type = Column(String, nullable=False)  # 'image', 'file'
    file_path = Column(String, nullable=False)
    mime_type = Column(String, nullable=True)
    extra_data = Column(JSON, nullable=True)  # Renamed from metadata to avoid SQLAlchemy conflict

    def __repr__(self):
        return f"<MessageAttachment(id={self.id}, type={self.attachment_type})>"
