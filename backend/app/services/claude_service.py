"""Claude API integration service."""
import anthropic
from typing import List, Dict, Any, Optional
from app.config import settings
import structlog

logger = structlog.get_logger()


class ClaudeService:
    """Service for interacting with Claude API."""

    def __init__(self):
        """Initialize Claude client."""
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.default_model = settings.default_claude_model

    async def generate_response(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
    ) -> Dict[str, Any]:
        """
        Generate a response from Claude using structured outputs (tool use).

        Args:
            system_prompt: System instructions for Claude
            messages: List of message dicts with 'role' and 'content'
            model: Model to use (defaults to configured model)
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature

        Returns:
            Dict containing response text, token usage, etc.
        """
        try:
            # Format messages for Anthropic API
            formatted_messages = []
            for msg in messages:
                formatted_messages.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })

            # Define the structured output schema using tool use
            tools = [
                {
                    "name": "respond",
                    "description": "Respond to the conversation with your message",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "message": {
                                "type": "string",
                                "description": "Your response message"
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
                                    "Each value must be ≤240 characters. "
                                    "Use this to record decisions, goals, constraints, strategies, or open questions "
                                    "that are broadly relevant to the whole conversation."
                                ),
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "action": {
                                            "type": "string",
                                            "enum": ["set", "remove"],
                                        },
                                        "key": {
                                            "type": "string",
                                            "description": "Short snake_case identifier, e.g. 'main_goal' or 'tech_stack'",
                                        },
                                        "entry_type": {
                                            "type": "string",
                                            "enum": ["goal", "decision", "constraint", "open_question", "strategy"],
                                        },
                                        "value": {
                                            "type": "string",
                                            "description": "Max 240 chars. Required for 'set' action.",
                                        },
                                        "reason": {
                                            "type": "string",
                                            "description": "Brief explanation for why you're making this change.",
                                        },
                                    },
                                    "required": ["action", "key", "reason"],
                                },
                            },
                        },
                        "required": ["message", "discussion_complete"]
                    }
                }
            ]

            logger.info(
                "calling_claude_api_with_structured_output",
                model=model or self.default_model,
                message_count=len(formatted_messages)
            )

            # Call Claude API with tool use for structured output
            response = self.client.messages.create(
                model=model or self.default_model,
                system=system_prompt,
                messages=formatted_messages,
                max_tokens=max_tokens,
                temperature=temperature,
                tools=tools,
                tool_choice={"type": "tool", "name": "respond"}  # Force using the respond tool
            )

            # Extract response from tool use
            content = ""
            discussion_complete = False
            propose_agent = ""
            whiteboard_updates = []
            if response.content:
                for block in response.content:
                    if block.type == "tool_use" and block.name == "respond":
                        content = block.input.get("message", "")
                        discussion_complete = block.input.get("discussion_complete", False)
                        propose_agent = block.input.get("propose_agent", "")
                        whiteboard_updates = block.input.get("whiteboard_updates", []) or []
                        break

            result = {
                "content": content,
                "discussion_complete": discussion_complete,
                "propose_agent": propose_agent or "",
                "whiteboard_updates": whiteboard_updates,
                "model": response.model,
                "stop_reason": response.stop_reason,
                "usage": {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                },
            }

            logger.info(
                "claude_api_success_structured",
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                message_length=len(content)
            )

            return result

        except anthropic.APIError as e:
            logger.error("claude_api_error", error=str(e))
            raise
        except Exception as e:
            logger.error("unexpected_error", error=str(e))
            raise


# Singleton instance
claude_service = ClaudeService()
