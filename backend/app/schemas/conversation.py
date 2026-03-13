"""Conversation schemas."""
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime


class ConversationBase(BaseModel):
    """Base conversation schema."""
    title: Optional[str] = Field(None, max_length=200)
    max_autonomous_turns: int = Field(20, ge=1, le=1000)
    requires_human_for_decisions: bool = True
    extra_data: Optional[Dict[str, Any]] = None


class ConversationCreate(ConversationBase):
    """Schema for creating a conversation."""
    initial_participants: Optional[List[str]] = Field(default_factory=list)


class ConversationUpdate(BaseModel):
    """Schema for updating a conversation."""
    title: Optional[str] = Field(None, max_length=200)
    status: Optional[str] = Field(None, pattern="^(active|paused|completed|archived)$")
    mode: Optional[str] = Field(None, pattern="^(interactive|autonomous)$")
    max_autonomous_turns: Optional[int] = Field(None, ge=1, le=1000)
    requires_human_for_decisions: Optional[bool] = None
    extra_data: Optional[Dict[str, Any]] = None


class ConversationResponse(ConversationBase):
    """Schema for conversation response."""
    id: str
    status: str
    mode: str
    created_at: datetime
    updated_at: datetime
    participant_ids: List[str] = Field(default_factory=list)

    class Config:
        from_attributes = True


class AddParticipantRequest(BaseModel):
    """Schema for adding a participant to a conversation."""
    agent_id: str = Field(..., min_length=1)
