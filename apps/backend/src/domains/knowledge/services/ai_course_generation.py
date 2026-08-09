import logging

from src.core.websocket import manager
from src.domains.knowledge.repository import knowledge_repo

logger = logging.getLogger(__name__)


async def generate_course_content_task(
    course_id: str,
    user_id: str,
    topic_prompt: str,
    difficulty: str = "BEGINNER",
) -> None:
    """
    Background task: generate a course outline via LLM and persist modules/topics.
    """
    try:
        await manager.send_to_user(
            user_id,
            {
                "type": "COURSE_UPDATE",
                "courseId": course_id,
                "status": "started",
                "message": "AI is thinking...",
            },
        )

        from src.domains.intelligence.reasoning.llm.llm_service import llm_service

        await manager.send_to_user(
            user_id,
            {
                "type": "COURSE_UPDATE",
                "courseId": course_id,
                "status": "processing",
                "message": "Structuring modules...",
            },
        )

        outline = await llm_service.generate_course_outline(
            topic=topic_prompt.strip(),
            difficulty=difficulty,
            user_message=None,
        )

        # Emit AI usage scoped to the course's workspace (Personal or Circle).
        try:
            from src.domains.billing.services.usage_tracking import (
                PERSONAL_USAGE_SCOPE,
                build_circle_usage_scope,
                emit_ai_usage,
            )

            course_row = await knowledge_repo.find_course(course_id, user_id)
            course_space_id = course_row.space_id if course_row else None
            if course_space_id:
                emit_scope = build_circle_usage_scope(course_space_id)
            else:
                emit_scope = PERSONAL_USAGE_SCOPE
            await emit_ai_usage(
                user_id=user_id,
                usage_scope=emit_scope,
                space_id=course_space_id,
                provider="gemini",
                model=None,
                feature="ai_course_generation",
                input_tokens=0,
                output_tokens=0,
                request_count=1,
            )
        except Exception:
            pass

        modules = outline.get("modules") or []
        if not modules:
            raise ValueError("Outline contained no modules")

        for i, mod_data in enumerate(modules):
            module_title = (mod_data.get("title") or f"Module {i + 1}").strip()
            topics = mod_data.get("topics") or []

            new_module = await knowledge_repo.create_module(
                {
                    "courseId": course_id,
                    "title": module_title,
                    "order": float(i),
                    "description": (mod_data.get("description") or "").strip() or None,
                }
            )

            for j, top in enumerate(topics):
                if isinstance(top, str):
                    title = top.strip()
                else:
                    title = str(top.get("title") or f"Topic {j + 1}").strip()
                if not title:
                    continue
                await knowledge_repo.create_topic(
                    {
                        "moduleId": new_module.id,
                        "title": title,
                        "order": float(j),
                        "estimatedHours": 0.5,
                    }
                )

        title = (outline.get("title") or "").strip() or f"Learning {topic_prompt[:80]}"
        description = (outline.get("description") or "").strip() or (
            f"A structured course on {topic_prompt[:200]}."
            + ("\u2026" if len(topic_prompt) > 200 else "")
        )
        diff_out = (outline.get("difficulty") or difficulty or "BEGINNER").upper()

        await knowledge_repo.update_course(
            course_id,
            {
                "title": title,
                "description": description,
                "difficulty": diff_out,
                "isAIGenerated": True,
            },
        )

        logger.info("AI generation complete for course %s", course_id)
        await manager.send_to_user(
            user_id,
            {
                "type": "COURSE_READY",
                "courseId": course_id,
                "status": "completed",
                "message": "Your course is ready!",
            },
        )

    except Exception as e:
        logger.error("AI generation failed for %s: %s", course_id, e)
        await manager.send_to_user(
            user_id,
            {
                "type": "COURSE_ERROR",
                "courseId": course_id,
                "message": "Failed to generate course.",
            },
        )
