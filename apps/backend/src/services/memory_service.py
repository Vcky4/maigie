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

from src.core.database import db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------


async def _call_gemini(prompt: str, max_tokens: int = 600) -> dict[str, Any] | None:
    """Call Gemini for JSON output. Returns parsed dict or None on failure."""
    try:
        import google.generativeai as genai
        import os

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return None

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            "models/gemini-2.0-flash-lite",
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=max_tokens,
                temperature=0.3,
            ),
        )
        response = await model.generate_content_async(prompt)
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

    Called after a meaningful conversation (≥4 user messages).
    Returns the created ConversationSummary record or None.
    """
    try:
        # Fetch messages for the session
        messages = await db.chatmessage.find_many(
            where={"sessionId": session_id, "reviewItemId": None},
            order={"createdAt": "asc"},
            take=50,
        )

        # Only summarize if there are enough messages
        user_msgs = [m for m in messages if m.role == "USER"]
        if len(user_msgs) < 4:
            return None

        # Check if already summarized
        existing = await db.conversationsummary.find_first(
            where={"sessionId": session_id, "userId": user_id}
        )
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
        action_logs = await db.aiactionlog.find_many(
            where={"message": {"sessionId": session_id}},
            take=20,
        )
        actions_taken = list({log.actionType for log in action_logs}) if action_logs else []

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

        record = await db.conversationsummary.create(
            data={
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
        summaries = await db.conversationsummary.find_many(
            where={"userId": user_id},
            order={"createdAt": "desc"},
            take=5,
        )
        if summaries:
            summary_lines = []
            for s in summaries:
                date_str = s.createdAt.strftime("%b %d") if s.createdAt else ""
                topics = ", ".join(s.keyTopics) if s.keyTopics else ""
                line = f"- [{date_str}] {s.summary}"
                if topics:
                    line += f" (Topics: {topics})"
                summary_lines.append(line)
            context_parts.append("Recent Conversation History:\n" + "\n".join(summary_lines))

        # 2. Active learning insights
        insights = await db.learninginsight.find_many(
            where={"userId": user_id, "isActive": True},
            order={"updatedAt": "desc"},
            take=10,
        )
        if insights:
            insight_lines = []
            for ins in insights:
                conf_str = (
                    f" ({int(ins.confidence * 100)}% confident)" if ins.confidence < 0.9 else ""
                )
                insight_lines.append(f"- [{ins.insightType}] {ins.content}{conf_str}")
            context_parts.append("Learning Insights About This User:\n" + "\n".join(insight_lines))

        # 3. Saved user facts (already used elsewhere, but include for completeness)
        facts = await db.userfact.find_many(
            where={"userId": user_id, "isActive": True},
            order={"updatedAt": "desc"},
            take=15,
        )
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
        facts = await db.userfact.find_many(
            where={"userId": user_id, "isActive": True},
            order={"confidence": "desc"},
            take=8,
        )
        if facts:
            fact_strs = [f.content for f in facts]
            parts.append("Known about user: " + "; ".join(fact_strs))

        # Key insights
        insights = await db.learninginsight.find_many(
            where={"userId": user_id, "isActive": True, "confidence": {"gte": 0.6}},
            order={"confidence": "desc"},
            take=5,
        )
        if insights:
            insight_strs = [f"{i.insightType}: {i.content}" for i in insights]
            parts.append("Learning patterns: " + "; ".join(insight_strs))

        # Study streak and recent activity
        streak = await db.userstreak.find_unique(where={"userId": user_id})
        if streak and streak.currentStreak > 0:
            parts.append(f"Current study streak: {streak.currentStreak} days")

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
        now = datetime.now(UTC)
        thirty_days_ago = now - timedelta(days=30)

        # 1. Study session patterns
        sessions = await db.studysession.find_many(
            where={
                "userId": user_id,
                "startTime": {"gte": thirty_days_ago},
            },
            order={"startTime": "asc"},
            take=100,
        )

        if len(sessions) >= 5:
            # Find optimal study time
            hour_counts: dict[int, int] = {}
            total_duration = 0.0
            for s in sessions:
                hour = s.startTime.hour
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
                await _upsert_insight(
                    user_id,
                    "optimal_time",
                    f"Most productive study time is in the {time_label} (around {peak_hour}:00). "
                    f"{len(sessions)} sessions in last 30 days.",
                    confidence=min(0.5 + len(sessions) * 0.02, 0.95),
                    data_points=len(sessions),
                )
                generated.append({"type": "optimal_time", "time": time_label})

            # Average session duration
            if total_duration > 0:
                avg_duration = total_duration / len(sessions)
                await _upsert_insight(
                    user_id,
                    "study_pattern",
                    f"Average study session lasts {avg_duration:.0f} minutes. "
                    f"Total study time: {total_duration:.0f} minutes in last 30 days.",
                    confidence=min(0.5 + len(sessions) * 0.02, 0.9),
                    data_points=len(sessions),
                )

        # 2. Course strengths/weaknesses
        courses = await db.course.find_many(
            where={"userId": user_id, "archived": False},
            include={"modules": {"include": {"topics": True}}},
            take=10,
        )

        for course in courses:
            total = sum(len(m.topics) for m in course.modules)
            completed = sum(1 for m in course.modules for t in m.topics if t.completed)
            if total >= 5:
                progress = round((completed / total) * 100) if total > 0 else 0
                if progress >= 80:
                    await _upsert_insight(
                        user_id,
                        "strength",
                        f"Strong progress in '{course.title}' ({progress}% complete).",
                        confidence=0.85,
                        data_points=total,
                    )
                elif progress < 20 and total >= 10:
                    await _upsert_insight(
                        user_id,
                        "weakness",
                        f"'{course.title}' needs attention — only {progress}% complete with {total} topics.",
                        confidence=0.7,
                        data_points=total,
                    )

        # 3. Review performance (spaced repetition)
        reviews = await db.reviewitem.find_many(
            where={
                "userId": user_id,
                "lastReviewedAt": {"gte": thirty_days_ago},
            },
            take=50,
        )
        if len(reviews) >= 3:
            avg_quality = sum(r.lastQuality for r in reviews if r.lastQuality >= 0) / max(
                1, sum(1 for r in reviews if r.lastQuality >= 0)
            )
            lapse_count = sum(r.lapseCount for r in reviews)
            if avg_quality >= 4:
                await _upsert_insight(
                    user_id,
                    "strategy_effectiveness",
                    f"Spaced repetition is working well — average recall quality is {avg_quality:.1f}/5.",
                    confidence=0.8,
                    data_points=len(reviews),
                )
            elif avg_quality < 2.5 and lapse_count > 3:
                await _upsert_insight(
                    user_id,
                    "strategy_effectiveness",
                    f"Review sessions show difficulty with recall (avg quality {avg_quality:.1f}/5, "
                    f"{lapse_count} lapses). Consider shorter review intervals.",
                    confidence=0.75,
                    data_points=len(reviews),
                )

        # 4. Schedule adherence
        behaviour_logs = await db.schedulebehaviourlog.find_many(
            where={
                "userId": user_id,
                "createdAt": {"gte": thirty_days_ago},
            },
            take=100,
        )
        if len(behaviour_logs) >= 5:
            completed_count = sum(1 for b in behaviour_logs if b.behaviourType == "COMPLETED")
            total_count = len(behaviour_logs)
            adherence = round((completed_count / total_count) * 100) if total_count > 0 else 0
            await _upsert_insight(
                user_id,
                "study_pattern",
                f"Schedule adherence rate: {adherence}% ({completed_count}/{total_count} blocks completed).",
                confidence=min(0.5 + total_count * 0.01, 0.9),
                data_points=total_count,
            )

    except Exception as e:
        logger.error("Failed to generate learning insights for user %s: %s", user_id, e)

    return generated


async def _upsert_insight(
    user_id: str,
    insight_type: str,
    content: str,
    confidence: float = 0.7,
    data_points: int = 1,
    metadata: dict | None = None,
) -> None:
    """Create or update a learning insight."""
    try:
        existing = await db.learninginsight.find_first(
            where={"userId": user_id, "insightType": insight_type, "isActive": True}
        )
        if existing:
            await db.learninginsight.update(
                where={"id": existing.id},
                data={
                    "content": content,
                    "confidence": confidence,
                    "dataPoints": data_points,
                    "metadata": metadata,
                },
            )
        else:
            await db.learninginsight.create(
                data={
                    "userId": user_id,
                    "insightType": insight_type,
                    "content": content,
                    "confidence": confidence,
                    "dataPoints": data_points,
                    "metadata": metadata,
                }
            )
    except Exception as e:
        logger.warning("Failed to upsert insight '%s': %s", insight_type, e)


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
        tasks = await db.aiagenttask.find_many(
            where={
                "userId": user_id,
                "status": "pending",
                "scheduledAt": {"lte": now},
            },
            order={"priority": "desc"},
            take=limit,
        )

        nudges = []
        for t in tasks:
            nudges.append(
                {
                    "id": t.id,
                    "type": t.taskType,
                    "title": t.title,
                    "message": t.message,
                    "priority": t.priority,
                    "actionData": t.actionData,
                }
            )
            # Mark as sent
            await db.aiagenttask.update(
                where={"id": t.id},
                data={"status": "sent", "sentAt": now},
            )

        return nudges
    except Exception as e:
        logger.warning("Failed to get pending nudges: %s", e)
        return []
