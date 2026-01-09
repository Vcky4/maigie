"""
RAG (Retrieval-Augmented Generation) Service.
Combines semantic search with LLM generation for personalized recommendations.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import json
import os
import re
from typing import Any

import google.generativeai as genai
from fastapi import HTTPException

from src.core.database import db
from src.services.embedding_service import embedding_service
from src.services.web_search_service import web_search_service

# Configure API
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))


class RAGService:
    """Service for retrieval-augmented generation."""

    def __init__(self):
        """Initialize the RAG service."""
        pass

    async def retrieve_relevant_context(
        self,
        query: str,
        user_id: str,
        object_types: list[str] | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Retrieve relevant context from user's data using semantic search.

        Args:
            query: The search query
            user_id: ID of the user
            object_types: Types of objects to search ("resource", "note", "course", "topic")
            limit: Maximum number of results

        Returns:
            List of relevant objects with their content and metadata
        """
        try:
            # Find similar embeddings
            similar_items = await embedding_service.find_similar(
                query_text=query,
                object_type=None,  # Search across all types
                limit=limit * 2,  # Get more results to filter by user
            )

            # Filter by user ownership and enrich with actual data
            enriched_results = []
            for item in similar_items:
                object_type = item["objectType"]
                object_id = item["objectId"]

                # Fetch the actual object based on type
                obj_data = await self._fetch_object(object_type, object_id, user_id)

                if obj_data:
                    enriched_results.append(
                        {
                            **item,
                            "data": obj_data,
                        }
                    )

                    if len(enriched_results) >= limit:
                        break

            return enriched_results

        except Exception as e:
            print(f"RAG retrieval error: {e}")
            raise HTTPException(status_code=500, detail="Failed to retrieve relevant context")

    async def _fetch_object(
        self, object_type: str, object_id: str, user_id: str
    ) -> dict[str, Any] | None:
        """
        Fetch an object from the database based on type and ID.

        Args:
            object_type: Type of object
            object_id: ID of the object
            user_id: User ID for ownership verification

        Returns:
            Object data or None if not found or not owned by user
        """
        try:
            if object_type == "resource":
                resource = await db.resource.find_first(where={"id": object_id, "userId": user_id})
                if resource:
                    return {
                        "id": resource.id,
                        "title": resource.title,
                        "url": resource.url,
                        "description": resource.description,
                        "type": resource.type,
                        "metadata": resource.metadata,
                    }

            elif object_type == "note":
                note = await db.note.find_first(where={"id": object_id, "userId": user_id})
                if note:
                    return {
                        "id": note.id,
                        "title": note.title,
                        "content": note.content,
                        "summary": note.summary,
                        "courseId": note.courseId,
                    }

            elif object_type == "course":
                course = await db.course.find_first(where={"id": object_id, "userId": user_id})
                if course:
                    return {
                        "id": course.id,
                        "title": course.title,
                        "description": course.description,
                        "difficulty": course.difficulty,
                    }

            elif object_type == "topic":
                topic = await db.topic.find_first(where={"id": object_id})
                if topic:
                    # Verify ownership through course
                    module = await db.module.find_first(
                        where={"id": topic.moduleId}, include={"course": True}
                    )
                    if module and module.course.userId == user_id:
                        return {
                            "id": topic.id,
                            "title": topic.title,
                            "content": topic.content,
                            "moduleId": topic.moduleId,
                        }

            return None

        except Exception as e:
            print(f"Error fetching object: {e}")
            return None

    async def generate_recommendations(
        self,
        query: str,
        user_id: str,
        user_context: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Generate personalized recommendations using RAG.

        Args:
            query: The user's query or intent
            user_id: ID of the user
            user_context: Additional user context (courses, goals, recent activity)
            limit: Maximum number of recommendations

        Returns:
            List of recommended resources with scores and explanations
        """
        try:
            # 1. Retrieve relevant context from user's data
            relevant_context = await self.retrieve_relevant_context(
                query=query, user_id=user_id, limit=5
            )

            # 2. Build context string for LLM
            context_parts = []
            if user_context:
                if user_context.get("courses"):
                    context_parts.append(
                        f"User's Courses: {', '.join([c.get('title', '') for c in user_context['courses']])}"
                    )
                if user_context.get("goals"):
                    context_parts.append(
                        f"User's Goals: {', '.join([g.get('title', '') for g in user_context['goals']])}"
                    )
                if user_context.get("recentActivity"):
                    context_parts.append(f"Recent Activity: {user_context['recentActivity']}")

            if relevant_context:
                context_parts.append("\nRelevant Content from User's Data:")
                for idx, item in enumerate(relevant_context[:3], 1):  # Top 3 most relevant
                    obj_data = item.get("data", {})
                    context_parts.append(
                        f"{idx}. {obj_data.get('title', 'Unknown')}: {obj_data.get('description') or obj_data.get('content', '')[:200]}"
                    )

            context_str = (
                "\n".join(context_parts) if context_parts else "No specific context available."
            )

            # 3. Perform actual web search to get real resources
            search_results = await web_search_service.search(query=query, max_results=limit * 2)

            if not search_results:
                # If no search results, return empty list
                return []

            # 4. Use LLM to format, filter, and rank the real search results
            search_results_str = "\n".join(
                [
                    f"{idx + 1}. Title: {r['title']}\n   URL: {r['url']}\n   Description: {r['description'][:300]}"
                    for idx, r in enumerate(search_results[: limit * 2])
                ]
            )

            recommendation_prompt = f"""You are an AI assistant helping a student find educational resources.

User Query: {query}

Context about the user:
{context_str}

Below are real web search results. Your task is to:
1. Select the most relevant resources from the search results
2. Format them properly with accurate information
3. Infer the resource type (VIDEO, ARTICLE, BOOK, COURSE, DOCUMENT, WEBSITE, PODCAST, or OTHER)
4. Provide a clear explanation of why each resource is relevant

Search Results:
{search_results_str}

IMPORTANT RULES:
- Use ONLY the URLs from the search results above. DO NOT make up URLs.
- Use the exact titles and descriptions from the search results
- Infer resource type based on URL domain and content (e.g., youtube.com = VIDEO, coursera.org = COURSE)
- Select the {limit} most relevant resources for the user's query and context
- Provide a clear "relevance" explanation for each resource

Format your response as a JSON array with this structure:
[
  {{
    "title": "Exact title from search results",
    "url": "Exact URL from search results",
    "description": "Description from search results (can be shortened/summarized)",
    "type": "VIDEO|ARTICLE|BOOK|COURSE|DOCUMENT|WEBSITE|PODCAST|OTHER",
    "relevance": "Explanation of why this resource is relevant to the user"
  }}
]

Return exactly {limit} recommendations, selecting the most relevant ones from the search results."""

            # Call LLM to format and rank the real search results
            temp_model = genai.GenerativeModel("models/gemini-flash-latest")
            response = await temp_model.generate_content_async(recommendation_prompt)
            response_text = response

            # Extract JSON from response
            response_text_str = (
                response_text.text if hasattr(response_text, "text") else str(response_text)
            )
            json_match = re.search(r"\[.*?\]", response_text_str, re.DOTALL)
            if json_match:
                recommendations = json.loads(json_match.group(0))
            else:
                # Fallback: try to parse entire response
                try:
                    recommendations = json.loads(response_text_str)
                except json.JSONDecodeError:
                    # If JSON parsing fails, fall back to using search results directly
                    recommendations = []
                    for result in search_results[:limit]:
                        recommendations.append(
                            {
                                "title": result.get("title", "Untitled"),
                                "url": result.get("url", ""),
                                "description": result.get("description", "")[:300],
                                "type": web_search_service.infer_resource_type(
                                    result.get("url", ""),
                                    result.get("title", ""),
                                    result.get("description", ""),
                                ),
                                "relevance": f"Found via web search for: {query}",
                            }
                        )

            # 5. Validate URLs are real (not example.com)
            validated_recommendations = []
            for rec in recommendations:
                url = rec.get("url", "")
                # Skip if URL is fake/placeholder
                if url and not url.startswith("https://example.com"):
                    validated_recommendations.append(rec)

            # If we don't have enough validated recommendations, add more from search results
            if len(validated_recommendations) < limit:
                used_urls = {rec.get("url") for rec in validated_recommendations}
                for result in search_results:
                    if result.get("url") not in used_urls and result.get("url"):
                        validated_recommendations.append(
                            {
                                "title": result.get("title", "Untitled"),
                                "url": result.get("url", ""),
                                "description": result.get("description", "")[:300],
                                "type": web_search_service.infer_resource_type(
                                    result.get("url", ""),
                                    result.get("title", ""),
                                    result.get("description", ""),
                                ),
                                "relevance": f"Found via web search for: {query}",
                            }
                        )
                        if len(validated_recommendations) >= limit:
                            break

            # 6. Score and rank recommendations
            scored_recommendations = []
            for rec in validated_recommendations[:limit]:
                # Calculate score based on relevance to query and user context
                score = self._calculate_recommendation_score(rec, query, relevant_context)
                scored_recommendations.append(
                    {
                        **rec,
                        "score": score,
                    }
                )

            # Sort by score
            scored_recommendations.sort(key=lambda x: x["score"], reverse=True)

            return scored_recommendations

        except Exception as e:
            print(f"RAG recommendation generation error: {e}")
            import traceback

            traceback.print_exc()
            raise HTTPException(status_code=500, detail="Failed to generate recommendations")

    def _calculate_recommendation_score(
        self,
        recommendation: dict[str, Any],
        query: str,
        relevant_context: list[dict[str, Any]],
    ) -> float:
        """
        Calculate a relevance score for a recommendation.

        Args:
            recommendation: The recommendation object
            relevant_context: Relevant context from user's data

        Returns:
            Score from 0.0 to 1.0
        """
        score = 0.5  # Base score

        # Boost score if recommendation mentions query keywords
        query_lower = query.lower()
        title_lower = recommendation.get("title", "").lower()
        desc_lower = recommendation.get("description", "").lower()

        # Check for keyword matches
        query_words = set(query_lower.split())
        title_words = set(title_lower.split())
        desc_words = set(desc_lower.split())

        # Title matches are more important
        title_overlap = len(query_words & title_words) / max(len(query_words), 1)
        desc_overlap = len(query_words & desc_words) / max(len(query_words), 1)

        score += title_overlap * 0.3
        score += desc_overlap * 0.2

        # Boost if recommendation type matches user's preferences (could be enhanced with user memory)
        # For now, we'll keep it simple

        return min(1.0, max(0.0, score))


# Global instance
rag_service = RAGService()
