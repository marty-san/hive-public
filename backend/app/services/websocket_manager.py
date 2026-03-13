"""WebSocket connection manager for real-time updates."""
from typing import Dict, Set
from fastapi import WebSocket
import structlog
import json

logger = structlog.get_logger()


class WebSocketManager:
    """Manages WebSocket connections for conversations."""

    def __init__(self):
        # Map of conversation_id -> set of WebSocket connections
        self.active_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, conversation_id: str):
        """Accept and register a WebSocket connection."""
        await websocket.accept()

        if conversation_id not in self.active_connections:
            self.active_connections[conversation_id] = set()

        self.active_connections[conversation_id].add(websocket)

        logger.info(
            "websocket_connected",
            conversation_id=conversation_id,
            connection_count=len(self.active_connections[conversation_id])
        )

    def disconnect(self, websocket: WebSocket, conversation_id: str):
        """Remove a WebSocket connection."""
        if conversation_id in self.active_connections:
            self.active_connections[conversation_id].discard(websocket)

            if not self.active_connections[conversation_id]:
                del self.active_connections[conversation_id]

            logger.info(
                "websocket_disconnected",
                conversation_id=conversation_id,
                remaining_connections=len(
                    self.active_connections.get(conversation_id, [])
                )
            )

    async def broadcast_to_conversation(
        self, conversation_id: str, message: dict
    ):
        """
        Broadcast a message to all connections for a conversation.

        Args:
            conversation_id: The conversation to broadcast to
            message: Dict to send as JSON
        """
        if conversation_id not in self.active_connections:
            return

        disconnected = set()
        for websocket in self.active_connections[conversation_id]:
            try:
                await websocket.send_json(message)
            except Exception as e:
                logger.error(
                    "websocket_send_error",
                    error=str(e),
                    conversation_id=conversation_id
                )
                disconnected.add(websocket)

        # Remove disconnected websockets
        for ws in disconnected:
            self.disconnect(ws, conversation_id)

    async def send_message_event(
        self, conversation_id: str, message_data: dict
    ):
        """
        Send a new message event to all conversation participants.

        Args:
            conversation_id: Conversation ID
            message_data: Message data to broadcast
        """
        await self.broadcast_to_conversation(
            conversation_id,
            {
                "type": "message.new",
                "conversation_id": conversation_id,
                "message": message_data
            }
        )

    async def send_agent_typing(
        self, conversation_id: str, agent_id: str, agent_name: str
    ):
        """
        Send agent typing indicator.

        Args:
            conversation_id: Conversation ID
            agent_id: Agent ID
            agent_name: Agent name
        """
        await self.broadcast_to_conversation(
            conversation_id,
            {
                "type": "agent.typing",
                "conversation_id": conversation_id,
                "agent_id": agent_id,
                "agent_name": agent_name
            }
        )

    async def send_whiteboard_event(
        self, conversation_id: str, entries: list, change: dict
    ):
        """
        Broadcast a whiteboard.updated event to all conversation participants.

        Args:
            conversation_id: Conversation ID
            entries: Full current whiteboard state (list of entry dicts)
            change: Dict describing the change (action, key, entry_type, value, author_name, reason)
        """
        await self.broadcast_to_conversation(
            conversation_id,
            {
                "type": "whiteboard.updated",
                "conversation_id": conversation_id,
                "entries": entries,
                "change": change,
            }
        )

    async def send_debug_event(
        self, conversation_id: str, event_type: str, data: dict
    ):
        """
        Send debug event to all conversation participants.

        Args:
            conversation_id: Conversation ID
            event_type: Type of debug event (e.g., 'agent_scoring', 'memory_retrieval', 'api_call')
            data: Debug data to broadcast
        """
        await self.broadcast_to_conversation(
            conversation_id,
            {
                "type": "debug.event",
                "conversation_id": conversation_id,
                "event_type": event_type,
                "timestamp": data.get("timestamp"),
                "data": data
            }
        )


# Singleton instance
ws_manager = WebSocketManager()
