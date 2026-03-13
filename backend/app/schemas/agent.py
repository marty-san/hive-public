"""Agent schemas."""
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime


class AgentBase(BaseModel):
    """Base agent schema."""
    name: str = Field(..., min_length=1, max_length=100)
    expertise_domain: str = Field(..., min_length=1)
    system_prompt: str = Field(..., min_length=1)
    communication_style: Optional[str] = None  # How agent should communicate (format, tone, structure)
    model: Optional[str] = None  # Claude model to use
    participation_criteria: Optional[Dict[str, Any]] = None
    extra_data: Optional[Dict[str, Any]] = None


class AgentCreate(AgentBase):
    """Schema for creating an agent."""
    pass


class AgentUpdate(BaseModel):
    """Schema for updating an agent."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    expertise_domain: Optional[str] = Field(None, min_length=1)
    system_prompt: Optional[str] = Field(None, min_length=1)
    communication_style: Optional[str] = None
    model: Optional[str] = None
    participation_criteria: Optional[Dict[str, Any]] = None
    extra_data: Optional[Dict[str, Any]] = None


class AgentResponse(AgentBase):
    """Schema for agent response."""
    id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
