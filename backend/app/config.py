"""Application configuration."""
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List


class Settings(BaseSettings):
    """Application settings."""

    # API Keys
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # Database
    database_url: str = "sqlite+aiosqlite:///./multi_agent_chat.db"

    # ChromaDB
    chroma_persist_directory: str = "./chroma_data"

    # Application
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    debug: bool = True

    # CORS
    cors_origins: List[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Default Model
    default_claude_model: str = "claude-sonnet-4-5-20250929"

    # Memory Settings
    max_working_memory_messages: int = 20
    max_episodic_retrieval: int = 10
    consolidation_threshold: int = 20

    # Temporal memory — decay lambdas (half-life = ln(2) / lambda)
    memory_lambda_state: float = 0.02        # ~35 day half-life for fact_state
    memory_lambda_preference: float = 0.005  # ~140 day half-life for observation_preference

    # Temporal memory — importance normalization
    memory_importance_max: int = 10

    # Temporal memory — supersession similarity threshold (mutable types without attribute_key)
    memory_supersession_similarity_threshold: float = 0.75

    # Temporal memory — type classification lists
    memory_mutable_types: List[str] = ["fact_state", "observation_preference", "fact", "observation"]
    memory_immutable_types: List[str] = ["event", "decision", "rejection", "reflection_summary"]

    # Conversation Settings
    default_max_autonomous_turns: int = 20
    max_context_tokens: int = 150000

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False
    )


# Global settings instance
settings = Settings()
