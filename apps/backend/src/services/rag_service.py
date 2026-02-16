"""
RAG (Retrieval-Augmented Generation) Service.
Combines semantic search with LLM generation for personalized recommendations.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

import asyncio
import hashlib
import json
import os
import re
from typing import Any

from fastapi import HTTPException
from google import genai
from google.genai import types

from src.core.cache import cache
from src.core.database import db
from src.services.embedding_service import embedding_service
from src.services.web_search_service import web_search_service

# Configure the google-genai client
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    print("⚠️ GEMINI_API_KEY not found in environment variables.")

# Create client for google-genai SDK
genai_client = genai.Client(api_key=api_key) if api_key else None


class RAGService:
    """Service for retrieval-augmented generation."""

    def __init__(self):
        """Initialize the RAG service."""
        pass  # No initialization needed - using google-genai client

    async def retrieve_relevant_context(
        self,
        query: str,
        user_id: str,
        object_types: list[str] | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Retrieve relevant context from user's data using semantic search.
        """
        try:
            cache_payload = {
                "q": (query or "").strip().lower(),
                "user_id": user_id,
                "object_types": object_types or [],
                "limit": limit,
            }
            cache_key = cache.make_key(
                [
                    "rag",
                    hashlib.sha256(
                        json.dumps(cache_payload, sort_keys=True).encode("utf-8")
                    ).hexdigest(),
                ]
            )
            cached_results = await cache.get(cache_key)
            if cached_results:
                return cached_results

            # Find similar embeddings
            similar_items = await embedding_service.find_similar(
                query_text=query,
                object_type=None,  # Search across all types
                limit=limit * 2,  # Get more results to filter by user
            )

            # Filter by user ownership and enrich with actual data
            items_to_fetch = similar_items[: limit * 2]
            fetch_tasks = [
                self._fetch_object(item["objectType"], item["objectId"], user_id)
                for item in items_to_fetch
            ]
            fetch_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

            enriched_results = []
            for item, obj_data in zip(items_to_fetch, fetch_results):
                if isinstance(obj_data, Exception) or not obj_data:
                    continue
                enriched_results.append({**item, "data": obj_data})
                if len(enriched_results) >= limit:
                    break

            await cache.set(cache_key, enriched_results, expire=600)
            return enriched_results

        except Exception as e:
            print(f"RAG retrieval error: {e}")
            raise HTTPException(status_code=500, detail="Failed to retrieve relevant context")

    async def _fetch_object(
        self, object_type: str, object_id: str, user_id: str
    ) -> dict[str, Any] | None:
        """
        Fetch an object from the database based on type and ID.
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

            elif object_type == "resource_bank_item":
                # Resource bank items are shared (not user-scoped), so no user check needed
                item = await db.resourcebankitem.find_first(
                    where={"id": object_id, "status": "APPROVED"},
                    include={"files": True},
                )
                if item:
                    return {
                        "id": item.id,
                        "title": item.title,
                        "description": item.description,
                        "type": str(item.type),
                        "universityName": item.universityName,
                        "courseName": item.courseName,
                        "courseCode": item.courseCode,
                        "fileCount": len(item.files) if item.files else 0,
                        "downloadCount": item.downloadCount,
                        "source": "resource_bank",
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
        Generate personalized recommendations using RAG with Google Search Grounding.
        """
        try:
            limit = max(1, int(limit))
            # 1. Retrieve relevant context from user's data
            relevant_context = await self.retrieve_relevant_context(
                query=query, user_id=user_id, limit=5
            )

            # 1b. Search resource bank for matching academic materials
            resource_bank_results = await self._search_resource_bank(query, user_id)

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

            if resource_bank_results:
                context_parts.append(
                    "\nMatching Resources from Resource Bank (academic materials shared by other students):"
                )
                for idx, rb_item in enumerate(resource_bank_results[:5], 1):
                    context_parts.append(
                        f"{idx}. [{rb_item.get('type', 'OTHER')}] {rb_item.get('title', 'Unknown')} "
                        f"- {rb_item.get('universityName', '')} {rb_item.get('courseCode', '')} "
                        f"(Downloads: {rb_item.get('downloadCount', 0)})"
                    )

            context_str = (
                "\n".join(context_parts) if context_parts else "No specific context available."
            )

            # 3. Use Gemini with Google Search Grounding (google-genai SDK)
            recommendation_prompt = f"""You are an AI assistant helping a student find educational resources.

User Query: {query}

Context about the user:
{context_str}

Based on the user's query and context, search the web and generate a list of educational resource recommendations.
For each recommendation, provide:
- Title (from the actual web page)
- URL (the real URL from your web search - DO NOT make up URLs)
- Description (summarize why this resource is useful)
- Resource type (VIDEO, ARTICLE, BOOK, COURSE, DOCUMENT, WEBSITE, PODCAST, or OTHER)
- Relevance explanation (why this resource is relevant to the user)

Format your response as a JSON array with this structure:
[
  {{
    "title": "Resource Title",
    "url": "https://real-url.com/resource",
    "description": "Why this resource is useful",
    "type": "VIDEO|ARTICLE|BOOK|COURSE|DOCUMENT|WEBSITE|PODCAST|OTHER",
    "relevance": "Explanation of why this resource is relevant to the user"
  }}
]

Return exactly {limit} high-quality recommendations with real URLs from your web search."""

            if not genai_client:
                raise HTTPException(status_code=500, detail="Gemini API key not configured")

            # Use google-genai SDK with Google Search grounding
            grounding_tool = types.Tool(google_search=types.GoogleSearch())
            config = types.GenerateContentConfig(
                tools=[grounding_tool],
                system_instruction="You are a helpful educational assistant that recommends learning resources.",
            )

            # Call generation with Google Search grounding
            response = await genai_client.aio.models.generate_content(
                model="gemini-2.0-flash",
                contents=recommendation_prompt,
                config=config,
            )

            response_text = response.text

            # Extract JSON from response - use greedy match to get complete array
            # The non-greedy \[.*?\] can cut off mid-string, so use greedy with proper boundary
            json_match = re.search(r"\[\s*\{.*\}\s*\]", response_text, re.DOTALL)

            recommendations = []
            if json_match:
                try:
                    recommendations = json.loads(json_match.group(0))
                except json.JSONDecodeError as e:
                    print(f"JSON Parse failed for recommendations: {e}")
                    # Try to extract individual objects and build array
                    try:
                        # Find all JSON objects in the response
                        objects = re.findall(r"\{[^{}]*\}", response_text)
                        for obj_str in objects:
                            try:
                                obj = json.loads(obj_str)
                                if "url" in obj and "title" in obj:
                                    recommendations.append(obj)
                            except json.JSONDecodeError:
                                continue
                    except Exception:
                        recommendations = []
            else:
                # Fallback: try to parse entire response
                try:
                    parsed = json.loads(response_text)
                    if isinstance(parsed, list):
                        recommendations = parsed
                    elif isinstance(parsed, dict) and "recommendations" in parsed:
                        recommendations = parsed["recommendations"]
                except json.JSONDecodeError:
                    # If JSON parsing fails, return empty list
                    print("JSON Parse failed for recommendations - no valid JSON found")
                    recommendations = []

            # 4. Validate URLs are real (not example.com) and infer resource types
            validated_recommendations = []
            for rec in recommendations:
                url = rec.get("url", "")
                # Skip if URL is fake/placeholder
                if url and not url.startswith("https://example.com"):
                    # Ensure resource type is set
                    if not rec.get("type") or rec.get("type") == "OTHER":
                        rec["type"] = web_search_service.infer_resource_type(
                            url,
                            rec.get("title", ""),
                            rec.get("description", ""),
                        )
                    validated_recommendations.append(rec)

            # 5. Score and rank recommendations
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

        except HTTPException:
            # Re-raise HTTP exceptions (like API key not configured)
            raise
        except Exception as e:
            print(f"RAG recommendation generation error: {e}")
            import traceback

            traceback.print_exc()
            # Return empty list instead of raising exception for graceful degradation
            # This allows the action service to return a helpful message
            return []

    async def _search_resource_bank(
        self,
        query: str,
        user_id: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Search the resource bank for relevant academic materials via Pinecone.
        Filters by the user's university if available.
        """
        try:
            # Get user's university for filtering
            user = await db.user.find_unique(where={"id": user_id})
            university_name = getattr(user, "universityName", None) if user else None

            # Use Pinecone metadata filter for university-scoped search
            metadata_filter: dict[str, Any] | None = None
            if university_name:
                metadata_filter = {"universityName": university_name}

            results = await embedding_service.find_similar(
                query_text=query,
                object_type="resource_bank_item",
                limit=limit,
                threshold=0.5,
                metadata_filter=metadata_filter,
            )

            # Enrich results with actual item data
            enriched = []
            for result in results:
                item_id = result.get("objectId")
                if not item_id:
                    continue

                item = await db.resourcebankitem.find_first(
                    where={"id": item_id, "status": "APPROVED"},
                    include={"files": True},
                )
                if item:
                    enriched.append(
                        {
                            "id": item.id,
                            "title": item.title,
                            "description": item.description,
                            "type": str(item.type),
                            "universityName": item.universityName,
                            "courseName": item.courseName,
                            "courseCode": item.courseCode,
                            "fileCount": len(item.files) if item.files else 0,
                            "downloadCount": item.downloadCount,
                            "similarity": result.get("similarity", 0),
                            "source": "resource_bank",
                        }
                    )

            return enriched

        except Exception as e:
            print(f"Error searching resource bank from RAG: {e}")
            return []

    def _calculate_recommendation_score(
        self,
        recommendation: dict[str, Any],
        query: str,
        relevant_context: list[dict[str, Any]],
    ) -> float:
        """
        Calculate a relevance score for a recommendation.
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
        if query_words:
            title_overlap = len(query_words & title_words) / max(len(query_words), 1)
            desc_overlap = len(query_words & desc_words) / max(len(query_words), 1)

            score += title_overlap * 0.3
            score += desc_overlap * 0.2

        return min(1.0, max(0.0, score))


# Global instance
rag_service = RAGService()
