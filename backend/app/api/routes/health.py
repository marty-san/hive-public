"""Health check routes."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.database import get_db
from app.schemas.common import HealthResponse

router = APIRouter()


@router.get("/api/health", response_model=HealthResponse)
async def health_check(db: AsyncSession = Depends(get_db)):
    """Health check endpoint."""
    # Test database connection
    try:
        result = await db.execute(text("SELECT 1"))
        db_status = "connected" if result else "disconnected"
    except Exception:
        db_status = "error"

    return HealthResponse(
        status="ok",
        version="0.1.0",
        database=db_status
    )
