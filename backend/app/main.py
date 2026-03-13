"""Main FastAPI application."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import structlog

from app.config import settings
from app.database import init_db
from app.api.routes import agents, conversations, messages, health, chat, websocket, tools, whiteboard

# Configure logging
logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events."""
    # Startup
    logger.info("Starting multi-agent chat application...")
    await init_db()
    logger.info("Database initialized")
    yield
    # Shutdown
    logger.info("Shutting down multi-agent chat application...")


# Create FastAPI app
app = FastAPI(
    title="Multi-Agent Chat API",
    description="API for multi-agent conversational system",
    version="0.1.0",
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health.router, tags=["health"])
app.include_router(agents.router, prefix="/api/agents", tags=["agents"])
app.include_router(conversations.router, prefix="/api/conversations", tags=["conversations"])
app.include_router(messages.router, prefix="/api/conversations", tags=["messages"])
app.include_router(chat.router, prefix="/api/conversations", tags=["chat"])
app.include_router(tools.router, prefix="/api/tools", tags=["tools"])
app.include_router(whiteboard.router, prefix="/api/conversations", tags=["whiteboard"])
app.include_router(websocket.router, tags=["websocket"])


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": "Multi-Agent Chat API",
        "version": "0.1.0",
        "docs": "/docs"
    }
