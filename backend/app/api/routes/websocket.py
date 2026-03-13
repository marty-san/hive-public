"""WebSocket routes for real-time updates."""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.services.websocket_manager import ws_manager
import structlog

logger = structlog.get_logger()
router = APIRouter()


@router.websocket("/ws/conversations/{conversation_id}")
async def websocket_endpoint(websocket: WebSocket, conversation_id: str):
    """
    WebSocket endpoint for real-time conversation updates.

    Clients connect to this endpoint to receive:
    - New messages
    - Agent typing indicators
    - Conversation state changes
    """
    await ws_manager.connect(websocket, conversation_id)

    try:
        # Keep connection alive and listen for messages
        while True:
            # Wait for messages from client (if any)
            data = await websocket.receive_text()

            # You can handle client messages here if needed
            logger.debug(
                "websocket_message_received",
                conversation_id=conversation_id,
                data=data
            )

    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, conversation_id)
        logger.info(
            "websocket_client_disconnected",
            conversation_id=conversation_id
        )
    except Exception as e:
        logger.error(
            "websocket_error",
            conversation_id=conversation_id,
            error=str(e)
        )
        ws_manager.disconnect(websocket, conversation_id)
