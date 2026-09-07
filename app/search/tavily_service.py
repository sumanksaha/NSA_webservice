"""Tavily web search service for NSA Webservice.

Integrates with Tavily API (https://tavily.ai) to perform web searches.
Used by /api/v2/search endpoint.
"""

import os
from typing import Any

import requests


class TavilySearchError(Exception):
    """Base exception for Tavily API errors."""


class TavilyAPIError(TavilySearchError):
    """Specific error for Tavily API responses with non-200 status codes."""


class TavilySearchService:
    """Service class for Tavily web search operations."""

    def __init__(self):
        self.api_key = os.environ.get("TAVILY_API_KEY")
        if not self.api_key:
            raise TavilyAPIError("Tavily API key not configured in environment")

    def _make_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _make_payload(self, query: str, limit: int = 10) -> dict[str, Any]:
        return {
            "query": query,
            "limit": limit,
            "include_images": True,
            "include_domains": [],  # Could be extended to filter domains
            "exclude_domains": [],  # Could be extended to exclude domains
        }

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Perform a Tavily web search.

        Args:
            query: Search query string
            limit: Number of results to return (1-10)

        Returns:
            List of search results (dictionaries with title, url, content, etc.)

        Raises:
            TavilyAPIError: If Tavily API returns non-200 status
        """
        payload = self._make_payload(query, limit)
        headers = self._make_headers()

        try:
            response = requests.post(
                "https://api.tavily.com/search",
                headers=headers,
                json=payload,
                timeout=10,
            )
        except requests.RequestException as e:
            raise TavilyAPIError(f"Network error: {e!s}") from e

        if response.status_code != 200:
            raise TavilyAPIError(f"Tavily API error {response.status_code}: {response.text}")

        try:
            data = response.json()
        except ValueError as e:
            raise TavilyAPIError(f"Invalid JSON response: {response.text}") from e

        return data.get("results", [])


# Register Tavily service as a global singleton
tavily_service = TavilySearchService()
