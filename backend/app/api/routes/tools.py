"""Tool routes for agent capabilities."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.services.tool_service import tool_service
import structlog

logger = structlog.get_logger()
router = APIRouter()


class WebSearchRequest(BaseModel):
    """Request for web search."""
    query: str
    max_results: int = 5


@router.post("/web-search")
async def web_search(request: WebSearchRequest):
    """
    Perform web search.

    Returns search results from DuckDuckGo.
    """
    try:
        results = await tool_service.web_search(
            query=request.query,
            max_results=request.max_results
        )

        return {
            "query": request.query,
            "results": results,
            "formatted": tool_service.format_search_results(results)
        }

    except Exception as e:
        logger.error("web_search_endpoint_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
