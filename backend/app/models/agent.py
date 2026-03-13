"""Agent model."""
from sqlalchemy import Column, String, Text, DateTime, JSON
from sqlalchemy.sql import func
from app.database import Base
import uuid


class Agent(Base):
    """Agent model representing an LLM-powered agent."""

    __tablename__ = "agents"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, unique=True, nullable=False, index=True)
    expertise_domain = Column(Text, nullable=False)
    system_prompt = Column(Text, nullable=False)
    communication_style = Column(Text, nullable=True)  # How agent should communicate (format, tone, structure)
    model = Column(String, nullable=True)  # Claude model to use (e.g., claude-sonnet-4-5-20250929)
    participation_criteria = Column(JSON, nullable=True)  # When agent should speak
    embedding = Column(JSON, nullable=True)  # Vector embedding of expertise_domain + system_prompt (1536 dimensions)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    extra_data = Column(JSON, nullable=True)  # Renamed from metadata to avoid SQLAlchemy conflict

    def __repr__(self):
        return f"<Agent(id={self.id}, name={self.name})>"
