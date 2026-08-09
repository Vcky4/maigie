"""
Long-Term Memory Service.

Provides conversation summarization, learning insight generation,
and memory-aware context retrieval for the agentic AI system.

Copyright (C) 2025 Maigie
Licensed under the Business Source License 1.1 (BUSL-1.1).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from ..repository import intelligence_repo

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------


async def _call_gemini(prompt: str, max_tokens: int = 600) -> dict[str, Any] | None:
    """Call Gemini for JSON output. Returns parsed dict or None on failure."""
    try:
        from google import genai
        from google.genai import types

        from src.domains.intelligence.reasoning.llm.registry import (
            LlmTask,
            default_model_for,
            gemini_api_key,
        )

        api_key = gemini_api_key()
        if not api_key:
            return None

        client = genai.Client(api_key=api_key)
        response = await client.aio.models.generate_content(
            model=default_model_for(LlmTask.MEMORY_JSON),
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=max_tokens,
                temperature=0.3,
            ),
        )
        text = (response.text or "").strip()
        if not text:
            return None

        # Extract JSON from response (handle markdown code blocks)
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            # Try array
            match = re.search(r"\[[\s\S]*\]", text)
            if match:
                return json.loads(match.group(0))
            return None
        return json.loads(match.group(0))
    except Exception as e:
        logger.warning("Gemini call for memory service failed: %s", e)
        return None


# ---------------------------------------------------------------------------
#  Conversation Summarization
# ---------------------------------------------------------------------------


async def summarize_conversation(session_id: str, user_id: str) -> dict | None:
    """
    Generate and store a summary for a chat session.

    Called after a meaningful conversation (>=4 user messages).
    Returns the created ConversationSummary record or None.
    """
    try:
        # Fetch messages for the session
        messages = await intelligence_repo.find_messages(
            session_id, take=50, order_asc=True, review_item_id=None
        )

        # Only summarize if there are enough messages
        user_msgs = [m for m in messages if m.role == "USER"]
        if len(user_msgs) < 4:
            return None

        # Check if already summarized
        existing = await intelligence_repo.find_summary_by_session(session_id, user_id)
        if existing:
            return existing

        # Build conversation text for summarization
        convo_lines = []
        for m in messages:
            role = "User" if m.role == "USER" else "Maigie"
            content = (m.content or "")[:300]
            convo_lines.append(f"{role}: {content}")

        convo_text = "\n".join(convo_lines[-30:])  # Last 30 messages max

        # Also check for AI actions
        action_logs = await intelligence_repo.find_action_logs(session_id=session_id)
        actions_taken = list({log.action_type for log in action_logs}) if action_logs else []

        prompt = f"""Analyze this study conversation and produce a JSON summary.

Conversation:
{convo_text}

Return a JSON object with:
- "summary": A 2-3 sentence summary of what was discussed and accomplished
- "key_topics": Array of 1-5 main topics/subjects discussed (strings)
- "emotional_tone": The user's general emotional state (one of: "motivated", "neutral", "frustrated", "curious", "stressed", "confident")
- "user_intent": What the user was trying to achieve in one sentence

Output only valid JSON, no markdown."""

        result = await _call_gemini(prompt, max_tokens=400)
        if not result:
            # Fallback: create a basic summary
            summary_text = f"Conversation with {len(user_msgs)} messages."
            result = {
                "summary": summary_text,
                "key_topics": [],
                "emotional_tone": "neutral",
            }

        record = await intelligence_repo.create_summary(
            {
                "userId": user_id,
                "sessionId": session_id,
                "summary": result.get("summary", "Conversation summary unavailable."),
                "keyTopics": result.get("key_topics", []),
                "actionsTaken": actions_taken,
                "emotionalTone": result.get("emotional_tone"),
            }
        )
        logger.info("Created conversation summary for session %s", session_id)
        return record

    except Exception as e:
        logger.error("Failed to summarize conversation %s: %s", session_id, e)
        return None


# ---------------------------------------------------------------------------
#  Memory Context Retrieval
# ---------------------------------------------------------------------------


async def get_memory_context(user_id: str, query: str | None = None) -> str:
    """
    Retrieve long-term memory context for the AI prompt.

    Returns a formatted string containing:
    - Recent conversation summaries
    - Active learning insights
    - Saved user facts
    """
    context_parts = []

    try:
        # 1. Recent conversation summaries (last 5)
        summaries = await intelligence_repo.list_summaries(user_id, take=5)
        if summaries:
            summary_lines = []
            for s in summaries:
                date_str = s.created_at.strftime("%b %d") if s.created_at else ""
                topics = ", ".join(s.key_topics) if s.key_topics else ""
                line = f"- [{date_str}] {s.summary}"
                if topics:
                    line += f" (Topics: {topics})"
                summary_lines.append(line)
            context_parts.append("Recent Conversation History:\n" + "\n".join(summary_lines))

        # 2. Active learning insights
        insights = await intelligence_repo.list_insights(user_id, active_only=True, take=10)
        if insights:
            insight_lines = []
            for ins in insights:
                conf_str = (
                    f" ({int(ins.confidence * 100)}% confident)" if ins.confidence < 0.9 else ""
                )
                insight_lines.append(f"- [{ins.insight_type}] {ins.content}{conf_str}")
            context_parts.append("Learning Insights About This User:\n" + "\n".join(insight_lines))

        # 3. Saved user facts
        facts = await intelligence_repo.list_user_facts(user_id, active_only=True, take=15)
        if facts:
            fact_lines = [f"- [{f.category}] {f.content}" for f in facts]
            context_parts.append("Remembered Facts About This User:\n" + "\n".join(fact_lines))

    except Exception as e:
        logger.warning("Failed to retrieve memory context: %s", e)

    if not context_parts:
        return ""

    return "\n\n".join(context_parts)


async def get_user_learning_profile(user_id: str) -> str:
    """
    Build a compressed learning profile for the system prompt.
    Includes key facts, insights, and behavioral patterns.
    """
    parts = []

    try:
        # Key facts
        facts = await intelligence_repo.list_user_facts(user_id, active_only=True, take=8)
        if facts:
            # Sort by confidence desc
            sorted_facts = sorted(facts, key=lambda f: f.confidence, reverse=True)
            fact_strs = [f.content for f in sorted_facts[:8]]
            parts.append("Known about user: " + "; ".join(fact_strs))

        # Key insights
        insights = await intelligence_repo.list_insights(
            user_id, active_only=True, min_confidence=0.6, take=5
        )
        if insights:
            insight_strs = [f"{i.insight_type}: {i.content}" for i in insights]
            parts.append("Learning patterns: " + "; ".join(insight_strs))

        # Study streak
        from src.domains.progress.repository import progress_repo

        streak = await progress_repo.get_streak(user_id)
        if streak and streak.current_streak > 0:
            parts.append(f"Current study streak: {streak.current_streak} days")

    except Exception as e:
        logger.warning("Failed to build learning profile: %s", e)

    return "\n".join(parts) if parts else ""


# ---------------------------------------------------------------------------
#  Learning Insight Generation
# ---------------------------------------------------------------------------


async def generate_learning_insights(user_id: str) -> list[dict]:
    """
    Analyze user behavior and generate/update learning insights.

    Looks at:
    - Study session patterns (optimal time, duration)
    - Course progress (strengths/weaknesses)
    - Review performance (spaced repetition quality)
    - Schedule adherence
    """
    generated = []

    try:
        from src.domains.knowledge.repository import KnowledgeRepository
        from src.domains.progress.repository import progress_repo

        knowledge_repo = KnowledgeRepository()

        now = datetime.now(UTC)
        thirty_days_ago = now - timedelta(days=30)

        # 1. Study session patterns
        sessions = await progress_repo.list_sessions(user_id, since=thirty_days_ago)

        if len(sessions) >= 5:
            # Find optimal study time
            hour_counts: dict[int, int] = {}
            total_duration = 0.0
            for s in sessions:
                hour = s.start_time.hour
                hour_counts[hour] = hour_counts.get(hour, 0) + 1
                total_duration += s.duration or 0

            if hour_counts:
                peak_hour = max(hour_counts, key=hour_counts.get)
                time_label = (
                    "morning"
                    if 5 <= peak_hour < 12
                    else (
                        "afternoon"
                        if 12 <= peak_hour < 17
                        else "evening" if 17 <= peak_hour < 21 else "night"
                    )
                )
                await intelligence_repo.upsert_insight(
                    user_id,
                    "optimal_time",
                    {
                        "content": f"Most productive study time is in the {time_label} (around {peak_hour}:00). "
                        f"{len(sessions)} sessions in last 30 days.",
                        "confidence": min(0.5 + len(sessions) * 0.02, 0.95),
                        "dataPoints": len(sessions),
                    },
                )
                generated.append({"type": "optimal_time", "time": time_label})

            # Average session duration
            if total_duration > 0:
                avg_duration = total_duration / len(sessions)
                await intelligence_repo.upsert_insight(
                    user_id,
                    "study_pattern",
                    {
                        "content": f"Average study session lasts {avg_duration:.0f} minutes. "
                        f"Total study time: {total_duration:.0f} minutes in last 30 days.",
                        "confidence": min(0.5 + len(sessions) * 0.02, 0.9),
                        "dataPoints": len(sessions),
                    },
                )

        # 2. Course strengths/weaknesses
        courses, _ = await knowledge_repo.list_courses(
            user_id, where={"archived": False}, skip=0, take=10
        )

        for course in courses:
            total = sum(len(m.topics) for m in course.modules)
            completed = sum(1 for m in course.modules for t in m.topics if t.completed)
            if total >= 5:
                progress = round((completed / total) * 100) if total > 0 else 0
                if progress >= 80:
                    await intelligence_repo.upsert_insight(
                        user_id,
                        "strength",
                        {
                            "content": f"Strong progress in '{course.title}' ({progress}% complete).",
                            "confidence": 0.85,
                            "dataPoints": total,
                        },
                    )
                elif progress < 20 and total >= 10:
                    await intelligence_repo.upsert_insight(
                        user_id,
                        "weakness",
                        {
                            "content": f"'{course.title}' needs attention — only {progress}% complete with {total} topics.",
                            "confidence": 0.7,
                            "dataPoints": total,
                        },
                    )

        # 3. Review performance (spaced repetition)
        reviews = await progress_repo.list_all_reviews(user_id)
        # Filter to those reviewed in last 30 days
        recent_reviews = [
            r for r in reviews if r.last_reviewed_at and r.last_reviewed_at >= thirty_days_ago
        ]
        if len(recent_reviews) >= 3:
            avg_quality = sum(r.last_quality for r in recent_reviews if r.last_quality >= 0) / max(
                1, sum(1 for r in recent_reviews if r.last_quality >= 0)
            )
            lapse_count = sum(r.lapse_count for r in recent_reviews)
            if avg_quality >= 4:
                await intelligence_repo.upsert_insight(
                    user_id,
                    "strategy_effectiveness",
                    {
                        "content": f"Spaced repetition is working well — average recall quality is {avg_quality:.1f}/5.",
                        "confidence": 0.8,
                        "dataPoints": len(recent_reviews),
                    },
                )
            elif avg_quality < 2.5 and lapse_count > 3:
                await intelligence_repo.upsert_insight(
                    user_id,
                    "strategy_effectiveness",
                    {
                        "content": f"Review sessions show difficulty with recall (avg quality {avg_quality:.1f}/5, "
                        f"{lapse_count} lapses). Consider shorter review intervals.",
                        "confidence": 0.75,
                        "dataPoints": len(recent_reviews),
                    },
                )

    except Exception as e:
        logger.error("Failed to generate learning insights for user %s: %s", user_id, e)

    return generated


# ---------------------------------------------------------------------------
#  Pending Nudges Retrieval
# ---------------------------------------------------------------------------


async def get_pending_nudges(user_id: str, limit: int = 5) -> list[dict]:
    """
    Retrieve pending AI agent tasks (nudges) for a user.
    Marks them as 'sent' after retrieval.
    """
    try:
        now = datetime.now(UTC)
        tasks = await intelligence_repo.list_pending_tasks(user_id, before=now, take=limit)

        nudges = []
        for t in tasks:
            nudges.append(
                {
                    "id": t.id,
                    "type": t.task_type,
                    "title": t.title,
                    "message": t.message,
                    "priority": t.priority,
                    "actionData": t.action_data,
                }
            )
            # Mark as sent
            await intelligence_repo.update_task(t.id, {"status": "sent", "sentAt": now})

        return nudges
    except Exception as e:
        logger.warning("Failed to get pending nudges: %s", e)
        return []
