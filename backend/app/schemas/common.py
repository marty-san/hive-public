"""Common schemas."""
from pydantic import BaseModel
from typing import Optional


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    version: str
    database: str


class ErrorResponse(BaseModel):
    """Error response."""
    detail: str
    error_code: Optional[str] = None
