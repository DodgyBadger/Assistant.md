"""Tavily search strategy."""

from __future__ import annotations

import httpx

from core.settings import get_default_api_timeout
from core.settings.secrets_store import get_secret_value
from core.web.errors import WebStrategyConfigurationError
from core.web.models import WebSearchItem, WebSearchResult


async def search_with_tavily(
    *, query: str, max_results: int, endpoint: str = "https://api.tavily.com/search"
) -> WebSearchResult:
    """Search Tavily and normalize its result shape."""
    api_key = get_secret_value("TAVILY_API_KEY")
    if not api_key:
        raise WebStrategyConfigurationError(
            "Tavily search requires the TAVILY_API_KEY secret"
        )
    async with httpx.AsyncClient(timeout=float(get_default_api_timeout())) as client:
        response = await client.post(
            endpoint,
            headers={"Content-Type": "application/json"},
            json={
                "api_key": api_key,
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
                "include_answer": False,
                "include_raw_content": False,
            },
        )
        response.raise_for_status()
    payload = response.json()
    items = [
        WebSearchItem(
            title=str(item.get("title") or "Untitled result"),
            url=str(item.get("url") or ""),
            snippet=str(item.get("content") or ""),
        )
        for item in payload.get("results", [])
        if isinstance(item, dict) and item.get("url")
    ]
    return WebSearchResult(
        strategy="tavily",
        query=query,
        items=items,
        metadata={"response_time": payload.get("response_time")},
    )
