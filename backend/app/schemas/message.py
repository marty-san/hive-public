"""Message schemas."""
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime


class MessageCreate(BaseModel):
    """Schema for creating a message."""
    conversation_id: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    sender_type: str = Field("human", pattern="^(human|agent|system)$")
    sender_id: Optional[str] = None
    attachments: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    extra_data: Optional[Dict[str, Any]] = None


class MessageResponse(BaseModel):
    """Schema for message response."""
    id: str
    conversation_id: str
    sender_type: str
    sender_id: Optional[str] = None
    sender_name: Optional[str] = None
    content: str
    created_at: datetime
    requires_human_decision: bool
    decision_resolved: bool
    extra_data: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True
