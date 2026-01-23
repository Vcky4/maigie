"""
Web Search Service.
Performs actual web searches to find real educational resources.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import asyncio
import time
from typing import Any

from duckduckgo_search import DDGS
from fastapi import HTTPException


class WebSearchService:
    """Service for performing web searches to find real resources."""

    def __init__(self):
        """Initialize the web search service."""
        pass

    async def search(
        self, query: str, max_results: int = 20, region: str = "us-en", max_retries: int = 3
    ) -> list[dict[str, Any]]:
        """
        Perform a web search and return real results with retry logic for rate limits.

        Args:
            query: The search query
            max_results: Maximum number of results to return
            region: Search region (default: "us-en")
            max_retries: Maximum number of retry attempts (default: 3)

        Returns:
            List of search results with title, url, description, and snippet
        """
        # Run the synchronous search in a thread pool to avoid blocking
        loop = asyncio.get_event_loop()

        for attempt in range(max_retries):
            try:
                results = await loop.run_in_executor(
                    None, self._perform_search, query, max_results, region
                )
                if results:
                    return results
                # If empty results and not last attempt, wait and retry
                if attempt < max_retries - 1:
                    wait_time = 2**attempt  # Exponential backoff: 1s, 2s, 4s
                    await asyncio.sleep(wait_time)
            except Exception as e:
                error_str = str(e).lower()
                # Check if it's a rate limit error
                is_rate_limit = (
                    "ratelimit" in error_str
                    or "202" in error_str
                    or "rate limit" in error_str
                    or "rate-limit" in error_str
                )

                if attempt < max_retries - 1:
                    if is_rate_limit:
                        # Exponential backoff: wait longer for rate limits
                        wait_time = (2**attempt) * 5  # 5s, 10s, 20s
                    else:
                        # Shorter backoff for other errors
                        wait_time = 2**attempt  # 1s, 2s, 4s
                    await asyncio.sleep(wait_time)
                    continue
                # Last attempt failed
                if is_rate_limit:
                    print(f"DuckDuckGo rate limit exceeded after {max_retries} attempts")
                else:
                    print(f"Web search error after {max_retries} attempts: {e}")
                return []

        # All retries exhausted, return empty list
        return []

    def _perform_search(self, query: str, max_results: int, region: str) -> list[dict[str, Any]]:
        """
        Perform the actual web search (synchronous).

        Args:
            query: The search query
            max_results: Maximum number of results
            region: Search region

        Returns:
            List of search results

        Raises:
            Exception: If search fails (will be caught by retry logic)
        """
        # Add a small delay to avoid hitting rate limits too quickly
        time.sleep(0.5)

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
