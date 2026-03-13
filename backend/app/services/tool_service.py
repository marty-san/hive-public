"""Tool service for agent capabilities."""
from typing import List, Dict, Any, Optional
import structlog

logger = structlog.get_logger()


class ToolService:
    """Service for agent tools like web search."""

    async def web_search(self, query: str, max_results: int = 5) -> List[Dict[str, Any]]:
        """
        Perform web search using DuckDuckGo.

        Args:
            query: Search query
            max_results: Maximum results to return

        Returns:
            List of search results
        """
        try:
            from duckduckgo_search import DDGS

            logger.info("performing_web_search", query=query)

            with DDGS() as ddgs:
                results = []
                for result in ddgs.text(query, max_results=max_results):
                    results.append({
                        "title": result.get("title", ""),
                        "url": result.get("href", ""),
                        "snippet": result.get("body", "")
                    })

                logger.info("web_search_complete", count=len(results))
                return results

        except ImportError:
            logger.error("duckduckgo_search_not_installed")
            return [{
                "title": "Web Search Unavailable",
                "url": "",
                "snippet": "Web search requires duckduckgo-search package. Install with: pip install duckduckgo-search"
            }]
        except Exception as e:
            logger.error("web_search_error", error=str(e))
            return [{
                "title": "Search Error",
                "url": "",
                "snippet": f"Error performing search: {str(e)}"
            }]

    def format_search_results(self, results: List[Dict[str, Any]]) -> str:
        """
        Format search results for agent context.

        Args:
            results: Search results

        Returns:
            Formatted string
        """
        if not results:
            return "No search results found."

        formatted = "Search Results:\n\n"
        for i, result in enumerate(results, 1):
            formatted += f"{i}. **{result['title']}**\n"
            formatted += f"   {result['snippet']}\n"
            if result['url']:
                formatted += f"   URL: {result['url']}\n"
            formatted += "\n"

        return formatted


# Singleton instance
tool_service = ToolService()
