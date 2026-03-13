"""Tool usage models."""
from sqlalchemy import Column, String, DateTime, Boolean, Integer, Float, JSON, ForeignKey
from sqlalchemy.sql import func
from app.database import Base
import uuid


class ToolUsageLog(Base):
    """Log of tool usage by agents."""

    __tablename__ = "tool_usage_logs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_id = Column(String, ForeignKey("agents.id"), nullable=False)
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False, index=True)
    message_id = Column(String, ForeignKey("messages.id"), nullable=False)
    tool_name = Column(String, nullable=False)  # 'web_search', 'image_analysis'
    tool_input = Column(JSON, nullable=False)
    tool_output = Column(JSON, nullable=True)
    requires_approval = Column(Boolean, default=False)
    approved = Column(Boolean, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    tokens_used = Column(Integer, nullable=True)
    cost_estimate = Column(Float, nullable=True)

    def __repr__(self):
        return f"<ToolUsageLog(id={self.id}, tool={self.tool_name})>"
