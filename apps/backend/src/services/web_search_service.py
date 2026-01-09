"""
Web Search Service.
Performs actual web searches to find real educational resources.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import asyncio
from typing import Any

from duckduckgo_search import DDGS
from fastapi import HTTPException


class WebSearchService:
    """Service for performing web searches to find real resources."""

    def __init__(self):
        """Initialize the web search service."""
        pass

    async def search(
        self, query: str, max_results: int = 20, region: str = "us-en"
    ) -> list[dict[str, Any]]:
        """
        Perform a web search and return real results.

        Args:
            query: The search query
            max_results: Maximum number of results to return
            region: Search region (default: "us-en")

        Returns:
            List of search results with title, url, description, and snippet
        """
        try:
            # Run the synchronous search in a thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None, self._perform_search, query, max_results, region
            )
            return results
        except Exception as e:
            print(f"Web search error: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to perform web search: {str(e)}")

    def _perform_search(self, query: str, max_results: int, region: str) -> list[dict[str, Any]]:
        """
        Perform the actual web search (synchronous).

        Args:
            query: The search query
            max_results: Maximum number of results
            region: Search region

        Returns:
            List of search results
        """
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results, region=region))
                formatted_results = []
                for result in results:
                    formatted_results.append(
                        {
                            "title": result.get("title", ""),
                            "url": result.get("href", ""),
                            "description": result.get("body", ""),
                            "snippet": result.get("body", "")[:200],  # First 200 chars
                        }
                    )
                return formatted_results
        except Exception as e:
            print(f"DuckDuckGo search error: {e}")
            # Return empty list instead of raising to allow graceful degradation
            return []

    def infer_resource_type(self, url: str, title: str, description: str) -> str:
        """
        Infer the resource type based on URL, title, and description.

        Args:
            url: The resource URL
            title: The resource title
            description: The resource description

        Returns:
            Resource type (VIDEO, ARTICLE, BOOK, COURSE, DOCUMENT, WEBSITE, PODCAST, or OTHER)
        """
        url_lower = url.lower()
        title_lower = title.lower()
        desc_lower = description.lower()
        combined = f"{url_lower} {title_lower} {desc_lower}"

        # Check for video platforms
        if any(
            domain in url_lower
            for domain in ["youtube.com", "youtu.be", "vimeo.com", "dailymotion.com"]
        ):
            return "VIDEO"

        # Check for podcast indicators
        if any(
            term in combined
            for term in ["podcast", "episode", "audio", "soundcloud", "spotify.com/podcast"]
        ):
            return "PODCAST"

        # Check for course platforms
        if any(
            domain in url_lower
            for domain in [
                "coursera.org",
                "udemy.com",
                "edx.org",
                "khanacademy.org",
                "udacity.com",
                "pluralsight.com",
                "skillshare.com",
            ]
        ):
            return "COURSE"

        # Check for book platforms
        if any(
            domain in url_lower
            for domain in [
                "amazon.com",
                "goodreads.com",
                "books.google.com",
                "bookshop.org",
            ]
        ) or any(term in combined for term in ["book", "ebook", "pdf book"]):
            return "BOOK"

        # Check for document types
        if url_lower.endswith((".pdf", ".doc", ".docx", ".ppt", ".pptx")):
            return "DOCUMENT"

        # Check for article indicators
        if any(
            term in combined
            for term in [
                "article",
                "blog",
                "tutorial",
                "guide",
                "medium.com",
                "dev.to",
                "wikipedia.org",
            ]
        ):
            return "ARTICLE"

        # Default to WEBSITE for other resources
        return "WEBSITE"


# Global instance
web_search_service = WebSearchService()
