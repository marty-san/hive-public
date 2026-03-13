"""LLM Router service - routes requests to appropriate LLM provider."""
from typing import List, Dict, Any, Optional
from app.services.claude_service import claude_service
from app.services.openai_service import openai_service
import structlog

logger = structlog.get_logger()


class LLMRouter:
    """Routes LLM requests to the appropriate provider based on model name."""

    def __init__(self):
        """Initialize router with available services."""
        self.claude_models = [
            "claude-opus-4-6",
            "claude-sonnet-4-6",
            "claude-opus-4-5-20251101",
            "claude-sonnet-4-5-20250929",
            "claude-sonnet-4-20250514",
            "claude-3-5-sonnet-20241022",
            "claude-3-5-sonnet-20240620",
            "claude-3-opus-20240229",
            "claude-3-sonnet-20240229",
            "claude-3-haiku-20240307",
        ]

        self.openai_models = [
            "gpt-4o",
            "gpt-4o-2024-11-20",
            "gpt-4o-mini",
            "gpt-4o-mini-2024-07-18",
            "o1",
            "o1-2024-12-17",
            "o1-mini",
            "o1-mini-2024-09-12",
            "gpt-4-turbo",
            "gpt-4",
            "gpt-3.5-turbo",
        ]

    def get_provider(self, model: str) -> str:
        """
        Determine which provider to use based on model name.

        Args:
            model: Model identifier

        Returns:
            Provider name ('claude' or 'openai')
        """
        # Check if it's a known Claude model
        if model in self.claude_models or model.startswith("claude-"):
            return "claude"

        # Check if it's a known OpenAI model
        if model in self.openai_models or model.startswith("gpt-") or model.startswith("o1"):
            return "openai"

        # Default to Claude for backwards compatibility
        logger.warning(
            "unknown_model_defaulting_to_claude",
            model=model
        )
        return "claude"

    async def generate_response(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        model: str,
        max_tokens: int = 4096,
        temperature: float = 1.0,
    ) -> Dict[str, Any]:
        """
        Route request to appropriate LLM provider.

        Args:
            system_prompt: System instructions
            messages: List of message dicts
            model: Model to use
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature

        Returns:
            Dict containing response text, token usage, etc.
        """
        provider = self.get_provider(model)

        logger.info(
            "routing_llm_request",
            model=model,
            provider=provider,
            message_count=len(messages)
        )

        if provider == "openai":
            # Special handling for o1 models (they don't support temperature/max_tokens the same way)
            if model.startswith("o1"):
                # o1 models use max_completion_tokens instead of max_tokens
                # and don't support temperature parameter
                return await openai_service.generate_response(
                    system_prompt=system_prompt,
                    messages=messages,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=1.0,  # o1 doesn't use this, but we pass it anyway
                )
            else:
                return await openai_service.generate_response(
                    system_prompt=system_prompt,
                    messages=messages,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
        else:
            return await claude_service.generate_response(
                system_prompt=system_prompt,
                messages=messages,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
            )


# Singleton instance
llm_router = LLMRouter()
