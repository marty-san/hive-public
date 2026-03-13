"""OpenAI API integration service."""
from openai import OpenAI
from typing import List, Dict, Any, Optional
from app.config import settings
import structlog
import json

logger = structlog.get_logger()


class OpenAIService:
    """Service for interacting with OpenAI API."""

    def __init__(self):
        """Initialize OpenAI client."""
        self.client = OpenAI(api_key=settings.openai_api_key)
        self.default_model = "gpt-4o"

    async def generate_response(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
    ) -> Dict[str, Any]:
        """
        Generate a response from OpenAI using structured outputs.

        Args:
            system_prompt: System instructions for the model
            messages: List of message dicts with 'role' and 'content'
            model: Model to use (defaults to gpt-4o)
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature

        Returns:
            Dict containing response text, token usage, etc.
        """
        try:
            # Format messages for OpenAI API (add system message at the start)
            formatted_messages = [
                {"role": "system", "content": system_prompt}
            ]

            for msg in messages:
                formatted_messages.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })

            actual_model = model or self.default_model

            # Check if model supports structured outputs (o1 models don't)
            supports_structured_output = not actual_model.startswith("o1")

            logger.info(
                "calling_openai_api",
                model=actual_model,
                message_count=len(formatted_messages),
                structured_output=supports_structured_output
            )

            # Prepare API call parameters
            api_params = {
                "model": actual_model,
                "messages": formatted_messages,
                "max_tokens": max_tokens,
            }

            # Only add temperature for non-o1 models
            if not actual_model.startswith("o1"):
                api_params["temperature"] = temperature

            # Add structured output for supported models
            if supports_structured_output:
                api_params["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "agent_response",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "message": {
                                    "type": "string",
                                    "description": "The agent's response message"
                                },
                                "discussion_complete": {
                                    "type": "boolean",
                                    "description": "Set to true if discussion has reached natural conclusion"
                                },
                                "propose_agent": {
                                    "type": "string",
                                    "description": (
                                        "Propose adding a new agent to the conversation. "
                                        "Describe the expertise gap, e.g. 'We need a legal expert to assess regulatory risk.' "
                                        "The system will search for a matching agent, hold a vote, and add them automatically. "
                                        "Leave empty string if not proposing anyone."
                                    )
                                },
                                "whiteboard_updates": {
                                    "type": "array",
                                    "description": (
                                        "Whiteboard changes to make after your response. "
                                        "Each value must be ≤240 characters."
                                    ),
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "action": {"type": "string"},
                                            "key": {"type": "string"},
                                            "entry_type": {"type": "string"},
                                            "value": {"type": "string"},
                                            "reason": {"type": "string"},
                                        },
                                        "required": ["action", "key", "entry_type", "value", "reason"],
                                        "additionalProperties": False,
                                    },
                                },
                            },
                            "required": ["message", "discussion_complete", "propose_agent", "whiteboard_updates"],
                            "additionalProperties": False
                        }
                    }
                }

            # Call OpenAI API
            response = self.client.chat.completions.create(**api_params)

            # Extract response
            content = response.choices[0].message.content if response.choices else ""
            discussion_complete = False

            # Parse JSON if using structured output
            propose_agent = ""
            whiteboard_updates = []
            if supports_structured_output and content:
                try:
                    parsed = json.loads(content)
                    content = parsed.get("message", content)
                    discussion_complete = parsed.get("discussion_complete", False)
                    propose_agent = parsed.get("propose_agent", "")
                    whiteboard_updates = parsed.get("whiteboard_updates", []) or []
                except json.JSONDecodeError:
                    logger.warning("failed_to_parse_structured_output", content_preview=content[:100])
                    # Fall back to raw content

            result = {
                "content": content or "",
                "discussion_complete": discussion_complete,
                "propose_agent": propose_agent or "",
                "whiteboard_updates": whiteboard_updates,
                "model": response.model,
                "stop_reason": response.choices[0].finish_reason if response.choices else None,
                "usage": {
                    "input_tokens": response.usage.prompt_tokens,
                    "output_tokens": response.usage.completion_tokens,
                },
            }

            logger.info(
                "openai_api_success",
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
                message_length=len(content or "")
            )

            return result

        except Exception as e:
            logger.error("openai_api_error", error=str(e))
            raise


# Singleton instance
openai_service = OpenAIService()
