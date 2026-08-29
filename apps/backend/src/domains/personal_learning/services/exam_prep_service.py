"""
Exam Prep service — full preparation lifecycle.

Manages preparations for exams, certifications, interviews, presentations,
assignments, and projects. AI extracts topics from uploaded materials and
generates day-by-day study plans.
"""

import logging
from datetime import UTC, datetime, timezone
from typing import Any

from src.shared.exceptions import ConflictError, MaigieError, NotFoundError

from ..repository import personal_learning_repo as repo
from . import prep_material_context

logger = logging.getLogger(__name__)


async def create_preparation(*, user_id: str, data: dict[str, Any]) -> Any:
    """
    Create a preparation record with status "SETUP".

    Req 4.1: Store subject, type (EXAM, CERTIFICATION, INTERVIEW, etc.), target date.
    """
    # Accept both the field name and the wire alias.
    target_date = data.get("target_date") or data.get("targetDate")
    if isinstance(target_date, str):
        target_date = datetime.fromisoformat(target_date.replace("Z", "+00:00"))

    prep_data = {
        "userId": user_id,
        "subject": data["subject"],
        # Persisted since migration 006. Previously accepted and discarded.
        "type": data.get("prep_type") or data.get("type"),
        "examDate": target_date,
        "description": data.get("description"),
        "status": "SETUP",
        # Persisted since migration 007. The wizard collected both and dropped them.
        "confidence": data.get("confidence"),
        "pace": data.get("pace"),
        # Persisted since migration 016. Optional: without a stated target the
        # workspace shows readiness with no target line, rather than a guessed one.
        "targetReadiness": data.get("target_readiness", data.get("targetReadiness")),
    }
    prep = await repo.create_exam_prep(prep_data)

    # Record in activity feed
    from . import activity_feed_service

    await activity_feed_service.record(
        user_id=user_id,
        activity_type="preparation_created",
        title=f"Started preparation: {data['subject']}",
        entity_type="preparation",
        entity_id=prep.id,
        context={"source": "personal", "prepId": prep.id, "type": prep_data["type"]},
    )

    # A preparation is a stated commitment with a date on it, so it earns a goal that measures its
    # own readiness. Quietly, because the preparation has already been created: failing this request
    # over the goal beside it would throw away work the learner just did.
    from src.domains.progress.services import goal_derivation_service

    await goal_derivation_service.derive_goals_quietly(user_id, prep_id=prep.id)

    return prep


async def get_preparation(*, user_id: str, prep_id: str) -> Any:
    """Get a preparation by ID. Raises NotFoundError if not found."""
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)
    return prep


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def get_preparation_detail(*, user_id: str, prep_id: str) -> dict[str, Any]:
    """One preparation with the progress and recommendation its workspace shows.

    Everything derived comes from `prep_readiness` and `prep_focus`, the same
    helpers the dashboard uses, so the workspace header cannot disagree with the
    card the learner clicked to reach it.

    The three reads run concurrently because none depends on another, and all three
    are already scoped: progress and topics by `prep_id`, the streak by `user_id`.
    """
    import asyncio

    from . import prep_focus, prep_readiness

    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    progress, topics, streak, topic_counts = await asyncio.gather(
        prep_readiness.load_for_preparation(prep_id),
        repo.list_prep_topics(prep_id),
        prep_readiness.load_practice_streak(user_id, prep_id=prep_id),
        repo.get_prep_topic_question_counts([prep_id]),
    )

    answered_by_topic = {
        topic_id: counts.get("answered_count", 0) for topic_id, counts in topic_counts.items()
    }
    focus = prep_focus.recommend(topics, answered_by_topic=answered_by_topic)

    now = datetime.now(UTC)
    delta = _as_utc(prep.exam_date) - now
    days_until_exam = delta.days if delta.total_seconds() >= 0 else None

    return {
        "id": prep.id,
        "userId": prep.user_id,
        "subject": prep.subject,
        # The field name, not the `type` wire alias: `prep_type` declares an
        # explicit validation alias, so the camel generator does not apply here.
        "prep_type": prep.prep_type,
        "examDate": prep.exam_date,
        "description": prep.description,
        "status": prep.status,
        "confidence": prep.confidence,
        "pace": prep.pace,
        "targetReadiness": prep.target_readiness,
        "createdAt": prep.created_at,
        "updatedAt": prep.updated_at,
        "daysUntilExam": days_until_exam,
        "progress": {
            "progressPercent": progress.progress_percent,
            "averageMasteryPercent": progress.average_mastery_percent,
            "targetReadiness": prep.target_readiness,
            "topicsTotal": progress.topics_total,
            "topicsStrong": progress.topics_strong,
            "topicsReview": progress.topics_review,
            "topicsFocus": progress.topics_focus,
            "topicsAssessed": progress.topics_assessed,
            "questionsAnswered": progress.questions_answered,
            "accuracyPercent": progress.accuracy_percent,
            "quizzesTaken": progress.quizzes_taken,
            "practiceMinutes": progress.practice_minutes,
            "practiceStreak": streak,
            "practiceReady": progress.practice_ready,
        },
        "focus": {
            "topicId": focus.topic_id,
            "topicTitle": focus.topic_title,
            "masteryPercent": focus.mastery_percent,
            "band": focus.band,
            "reasonCode": focus.reason_code,
            "reason": focus.reason,
            "recommendedMode": focus.recommended_mode,
            "recommendedQuestionCount": focus.recommended_question_count,
            "estimatedMinutes": focus.estimated_minutes,
        },
    }


# `list_preparations` was removed. It fetched every preparation and sorted in
# Python; `search_preparations` below does the same ordering in SQL with
# filtering and pagination, and is what the route uses.


async def search_preparations(
    *,
    user_id: str,
    status: str | None = None,
    search: str | None = None,
    sort_by: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Any], int]:
    """Filtered, paginated preparations with optional sorting.

    sort_by: None/"date" for target date, "readiness" for average mastery
    """
    return await repo.search_exam_preps(
        user_id,
        status=status,
        search=search,
        sort_by=sort_by,
        skip=(page - 1) * page_size,
        take=page_size,
    )


async def update_preparation(*, user_id: str, prep_id: str, data: dict[str, Any]) -> Any:
    """Update preparation fields.

    Translates the request's field names onto the repository's wire names and
    parses the target date, so the route does not have to.
    """
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    mapped: dict[str, Any] = {}
    if "subject" in data:
        mapped["subject"] = data["subject"]
    if "description" in data:
        mapped["description"] = data["description"]
    if "status" in data:
        mapped["status"] = data["status"]
    if "prep_type" in data:
        mapped["type"] = data["prep_type"]
    for intent_field in ("confidence", "pace"):
        if intent_field in data:
            mapped[intent_field] = data[intent_field]
    if "target_readiness" in data:
        mapped["targetReadiness"] = data["target_readiness"]
    for key in ("target_date", "exam_date"):
        if key in data and data[key] is not None:
            value = data[key]
            if isinstance(value, str):
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            mapped["examDate"] = value

    if not mapped:
        return prep
    return await repo.update_exam_prep(prep_id, mapped)


async def delete_preparation(*, user_id: str, prep_id: str) -> bool:
    """Delete a preparation."""
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        return False
    await repo.delete_exam_prep(prep_id)
    return True


async def upload_material(*, user_id: str, prep_id: str, data: dict[str, Any]) -> Any:
    """
    Upload material to a preparation.

    Req 4.2: Store material and trigger AI extraction of key topics.
    """
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    material_data = {
        "prepId": prep_id,
        "filename": data["filename"],
        "url": data["url"],
        "fileType": data.get("fileType"),
        "size": data.get("size"),
        "extractedText": data.get("extractedText"),
        "category": data.get("category", "OTHER"),
        "label": data.get("label"),
    }

    material = await repo.create_prep_material(material_data)

    # Update preparation status to IN_PROGRESS if still in SETUP
    if prep.status == "SETUP":
        await repo.update_exam_prep(prep_id, {"status": "IN_PROGRESS"})

    return material


# Uploads are capped well below what a textbook would be. The cap exists because
# extraction reads the whole file into memory, and because a 200MB scan is not
# material a learner is going to revise from.
MAX_MATERIAL_UPLOAD_BYTES = 25 * 1024 * 1024

# Text is extracted for these, so topic extraction has something to read. Other
# types are stored and downloadable but contribute nothing to extraction, and
# `hasExtractedText` says so rather than the client having to guess from the
# extension.
_TEXT_EXTENSIONS = (".txt", ".md", ".markdown", ".csv")


def _safe_filename(raw: str | None) -> str:
    """Reduce a client-supplied filename to something safe to use as a path segment.

    Only the basename is kept and the character set is restricted, so a name like
    `../../other-user/notes.pdf` cannot write outside the preparation's own prefix.
    """
    import re

    candidate = (raw or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "_", candidate).strip("._")
    return candidate[:200] or "material"


def _extract_upload_text(content: bytes, filename: str, content_type: str | None) -> str | None:
    """Pull readable text out of an uploaded file, or return None.

    Returning `None` is a normal outcome, not an error: an image or a slide deck is
    still worth storing. Extraction failure is also `None` rather than an
    exception, because a file the learner can open is worth keeping even if we
    cannot read it.
    """
    lowered = filename.lower()

    if lowered.endswith(_TEXT_EXTENSIONS) or (content_type or "").startswith("text/"):
        try:
            return content.decode("utf-8", errors="replace").strip() or None
        except Exception:  # noqa: BLE001 - a file we cannot decode is still storable
            return None

    if lowered.endswith(".pdf") or (content_type or "") == "application/pdf":
        try:
            import io

            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(content))
            pages = [page.extract_text() or "" for page in reader.pages]
            text = "\n\n".join(part.strip() for part in pages if part.strip())
            return text or None
        except Exception as e:  # noqa: BLE001 - a scanned PDF has no text layer
            logger.info(
                "PDF text extraction produced nothing",
                extra={"filename": filename, "error": type(e).__name__},
            )
            return None

    return None


async def upload_material_file(
    *,
    user_id: str,
    prep_id: str,
    file: Any,
    category: str = "OTHER",
    label: str | None = None,
) -> Any:
    """Store an uploaded file and register it as material.

    The JSON create path requires a `url`, which meant the workspace's file picker
    and the create wizard's drag-and-drop had nowhere to send a file: there was no
    upload endpoint anywhere in the API and no direct-to-storage path on the web
    client. This closes that, using the same `storage_service` the rest of the
    platform writes through.

    Text is extracted on the way in where the format allows it, because extracted
    text is what topic extraction reads. A file we cannot read is still stored;
    `hasExtractedText` reports the difference so the client can tell the learner
    that a scanned PDF will not produce topics.
    """
    from src.shared.infrastructure.storage import StorageError, storage_service

    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    filename = _safe_filename(getattr(file, "filename", None))
    content = await file.read()
    size = len(content)

    if size == 0:
        raise MaigieError(
            "That file is empty.",
            status_code=400,
            code="MATERIAL_FILE_EMPTY",
        )
    if size > MAX_MATERIAL_UPLOAD_BYTES:
        raise MaigieError(
            f"That file is larger than the {MAX_MATERIAL_UPLOAD_BYTES // (1024 * 1024)}MB limit.",
            status_code=413,
            code="MATERIAL_FILE_TOO_LARGE",
        )

    content_type = getattr(file, "content_type", None)

    try:
        # Pathed by preparation under the learner's own id, so one learner's
        # uploads can never collide with or overwrite another's. The filename is
        # sanitised first, so a crafted name cannot escape that prefix.
        stored = await storage_service.upload_bytes(
            content,
            f"prep-materials/{user_id}/{prep_id}/{filename}",
            content_type=content_type or "application/octet-stream",
        )
    except StorageError as e:
        logger.warning(
            "Material upload to storage failed",
            extra={"prep_id": prep_id, "filename": filename},
        )
        raise MaigieError(
            "We could not store that file. Please try again.",
            status_code=503,
            code="MATERIAL_UPLOAD_FAILED",
        ) from e

    material = await repo.create_prep_material(
        {
            "prepId": prep_id,
            "filename": filename,
            "url": stored.get("url") or stored.get("path") or "",
            "fileType": content_type,
            "size": size,
            "extractedText": _extract_upload_text(content, filename, content_type),
            "category": category or "OTHER",
            "label": label,
        }
    )

    if prep.status == "SETUP":
        await repo.update_exam_prep(prep_id, {"status": "IN_PROGRESS"})

    return material


async def list_materials(*, user_id: str, prep_id: str) -> list[dict[str, Any]]:
    """List materials for a preparation.

    Returns listing shapes that omit `extractedText`: it can hold an entire
    chapter per row and is not needed to render a material list.
    """
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    materials = await repo.list_prep_materials(prep_id)
    return [
        {
            "id": material.id,
            "prepId": material.prep_id,
            "filename": material.filename,
            "url": material.url,
            "fileType": material.file_type,
            "size": material.size,
            "category": material.category,
            "label": material.label,
            "hasExtractedText": bool(material.extracted_text),
            "createdAt": material.created_at,
            "updatedAt": material.updated_at,
        }
        for material in materials
    ]


async def update_material(
    *, user_id: str, prep_id: str, material_id: str, data: dict[str, Any]
) -> Any:
    """Update a material's category or label."""
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    material = await repo.find_prep_material(material_id, prep_id)
    if not material:
        raise NotFoundError("PrepMaterial", material_id)

    payload = {key: value for key, value in data.items() if key in ("category", "label")}
    if not payload:
        return material
    return await repo.update_prep_material(material_id, payload)


async def delete_material(*, user_id: str, prep_id: str, material_id: str) -> bool:
    """Delete a material from a preparation."""
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    material = await repo.find_prep_material(material_id, prep_id)
    if not material:
        return False
    await repo.delete_prep_material(material_id)
    return True


async def update_topic(*, user_id: str, prep_id: str, topic_id: str, data: dict[str, Any]) -> Any:
    """Update a topic belonging to a preparation."""
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    topic = await repo.find_prep_topic(topic_id, prep_id)
    if not topic:
        raise NotFoundError("PrepTopic", topic_id)

    field_map = {
        "title": "title",
        "description": "description",
        "category": "category",
        "estimated_minutes": "estimatedMinutes",
        "order_index": "orderIndex",
        "mastery_score": "masteryScore",
        "target_mastery": "targetMastery",
        "status": "status",
    }
    payload = {field_map[k]: v for k, v in data.items() if k in field_map}
    if not payload:
        return _topic_payload(topic)
    updated = await repo.update_prep_topic(topic_id, payload)
    return _topic_payload(updated or topic)


async def delete_topic(*, user_id: str, prep_id: str, topic_id: str) -> bool:
    """Delete a topic from a preparation."""
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    topic = await repo.find_prep_topic(topic_id, prep_id)
    if not topic:
        return False
    await repo.delete_prep_topic(topic_id)
    return True


async def extract_topics(*, user_id: str, prep_id: str) -> list[Any]:
    """
    AI-extract key topics from preparation materials.

    Req 4.3: Create topic records with titles, descriptions, and estimated study time.

    Routed through `llm_resilient` rather than calling the provider directly, so
    extraction gets the learner's configured provider and the per-provider circuit
    breaker that quiz generation already had.

    A failure raises. It previously returned `[]`, which reached the client as a
    `200` with an empty array — indistinguishable from "this material has no
    topics in it", and the caller's next step (start practising) then failed with
    a different error entirely.
    """
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)
    # Nothing new to extract for an exam that has already happened. The topics already there stay
    # readable; see `ensure_accepts_new_work`.
    ensure_accepts_new_work(prep)

    from . import llm_resilient

    # Gather material text.
    #
    # Selection and budgeting live in `prep_material_context` because this used to
    # join every material and slice 5,000 characters off the *joined* string. Two
    # consequences, both measured: only 3.1% of a 162,885-character document was read,
    # and any file behind the cap contributed nothing at all — so a syllabus uploaded
    # after a textbook was invisible to the one step that most needed it.
    materials = await repo.list_prep_materials(prep_id)
    context = prep_material_context.select(
        materials, budget=prep_material_context.TOPIC_EXTRACTION_BUDGET
    )

    # No uploaded text is not a blocker: the subject and description are enough to
    # get a usable topic list, which is what keeps a preparation created without
    # files from being permanently unable to practise.
    if context.has_text:
        material_text = context.as_prompt_block()
        logger.info(
            "Topic extraction material context",
            extra={
                "prep_id": prep_id,
                "files_read": len(context.excerpts),
                "files_omitted": len(context.omitted),
                "stored_chars": context.stored_chars,
                "used_chars": context.used_chars,
            },
        )
    else:
        material_text = f"Subject: {prep.subject}\nDescription: {prep.description or ''}"

    prompt = (
        f"Analyze this learning material and extract the key topics for study.\n"
        f"Subject: {prep.subject}\n"
        f"Materials:\n{material_text}\n\n"
        f"Return a JSON array of topic objects with:\n"
        f"- 'title': short topic name\n"
        f"- 'description': brief description of what to learn\n"
        f"- 'category': a short grouping heading shared by related topics, e.g. "
        f"'Foundations', 'Core concepts', 'Applications'. Reuse the same heading "
        f"across topics that belong together; use at most 5 distinct headings.\n"
        f"- 'estimatedMinutes': estimated study time in minutes (15-120)\n\n"
        f"Generate 5-15 topics covering all important areas.\n"
        f"Return ONLY the JSON array."
    )

    try:
        topics_data = await llm_resilient.generate_content_json(
            prompt, max_tokens=3000, user_id=user_id
        )
    except Exception as e:
        logger.warning(
            "Topic extraction failed",
            extra={"prep_id": prep_id, "error": type(e).__name__},
        )
        raise MaigieError(
            "We could not extract topics from this material. Please try again.",
            status_code=503,
            code="PREP_TOPIC_EXTRACTION_FAILED",
        ) from e

    if not isinstance(topics_data, list):
        logger.warning(
            "Topic extraction returned a non-list payload",
            extra={"prep_id": prep_id, "payload_type": type(topics_data).__name__},
        )
        raise MaigieError(
            "We could not extract topics from this material. Please try again.",
            status_code=503,
            code="PREP_TOPIC_EXTRACTION_FAILED",
        )

    created_topics = []
    for idx, topic in enumerate(topics_data):
        if isinstance(topic, dict) and topic.get("title"):
            prep_topic = await repo.create_prep_topic(
                {
                    "prepId": prep_id,
                    "title": topic["title"],
                    "description": topic.get("description"),
                    "category": _normalize_category(topic.get("category")),
                    "estimatedMinutes": topic.get("estimatedMinutes", 30),
                    "orderIndex": idx,
                    "status": "NOT_STARTED",
                }
            )
            created_topics.append(prep_topic)

    if not created_topics:
        # A well-formed response containing nothing usable is still a failure, and
        # saying so is better than an empty `200` the caller has to interpret.
        raise MaigieError(
            "We could not extract topics from this material. Please try again.",
            status_code=503,
            code="PREP_TOPIC_EXTRACTION_FAILED",
        )

    return [_topic_payload(topic) for topic in created_topics]


# A heading has to fit in a group label, and an unbounded string from a model does
# not. Longer values are dropped rather than truncated: half a heading is worse
# than none, because the client would still render it.
_MAX_CATEGORY_LENGTH = 60


def _normalize_category(value: Any) -> str | None:
    """Keep a usable grouping heading, or nothing."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or len(cleaned) > _MAX_CATEGORY_LENGTH:
        return None
    return cleaned


def _topic_payload(topic: Any, counts: dict[str, int] | None = None) -> dict[str, Any]:
    """Serialise a topic with its band, and its counts when they were loaded.

    The band is resolved here rather than on the client so that every surface uses
    the same 70/80 boundaries as the dashboard and the readiness helper.
    """
    from . import prep_readiness

    payload: dict[str, Any] = {
        "id": topic.id,
        "prepId": topic.prep_id,
        "title": topic.title,
        "description": topic.description,
        "category": topic.category,
        "estimatedMinutes": topic.estimated_minutes,
        "orderIndex": topic.order_index,
        "masteryScore": topic.mastery_score,
        "targetMastery": topic.target_mastery,
        "band": prep_readiness.mastery_band(topic.mastery_score),
        "status": topic.status,
        "createdAt": topic.created_at,
    }
    if counts is not None:
        payload["questionCount"] = counts.get("question_count", 0)
        payload["answeredQuestionCount"] = counts.get("answered_count", 0)
    return payload


async def list_topics(*, user_id: str, prep_id: str) -> list[dict[str, Any]]:
    """List topics for a preparation, with each topic's question counts.

    The counts ("43 of 46 answered") are an aggregate over the question bank and
    answers, not columns on the topic. They are included here because the
    alternative — the workspace calling the paginated bank endpoint once per topic
    — is a request per topic for a number the database can produce in one query.
    """
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    topics = await repo.list_prep_topics(prep_id)
    counts = await repo.get_prep_topic_question_counts([prep_id])
    return [
        _topic_payload(topic, counts.get(topic.id, {"question_count": 0, "answered_count": 0}))
        for topic in topics
    ]


#: Statuses in which a preparation no longer takes on new study work.
#:
#: `AWAITING_REVIEW` and `COMPLETED` are grouped because the learner's *situation* is the same in both: the
#: exam has happened. `COMPLETED` differs only in that they have told us how it went.
CLOSED_TO_NEW_WORK = ("AWAITING_REVIEW", "COMPLETED")


def ensure_accepts_new_work(prep: Any) -> None:
    """Refuse to generate new study work for a preparation whose exam is behind the learner.

    **Reads stay open; only generation closes.** The learner keeps every topic, every banked question, the
    readiness history and the timeline — that material is the record of what they did, and hiding it would
    be deleting their work in effect. What stops is *making more of it*: extracting further topics from
    material for an exam that has happened, or starting a practice quiz for it.

    The reason is not tidiness. A quiz taken after the exam writes `QuizSession` rows and moves topic
    mastery, which feeds `averageMasteryPercent` — the readiness figure §6.2 wants to score against the
    recorded outcome. Practising afterwards rewrites the prediction after the result is known, which is
    precisely the measurement calibration cannot survive. A learner who wants to keep practising this
    material wants a new preparation, or Learn.

    Two messages rather than one, because the two states leave the learner in different places: an awaiting
    preparation has something for them to *do*, and a completed one does not.

    Raises `ConflictError` (409). Nothing about the request is malformed — the preparation is simply past the
    point where this is what it is for.
    """
    status = getattr(prep, "status", None)
    if status not in CLOSED_TO_NEW_WORK:
        return
    if status == "AWAITING_REVIEW":
        raise ConflictError(
            "This preparation is waiting for your review",
            detail=(
                "Its exam has passed, so there is no more practice to schedule for it. Tell us how it "
                "went to close it — everything you have already built stays where it is."
            ),
            # Named rather than the generic `CONFLICT`, matching `PREP_TOPICS_REQUIRED` and
            # `PREP_MATERIAL_REQUIRED`. A client that only knows "something conflicted" can do nothing but
            # print the message; one that knows *which* rule can offer the review instead of a retry.
            code="PREP_AWAITING_REVIEW",
        )
    raise ConflictError(
        "This preparation is finished",
        detail=(
            "Its exam has passed and you have already reviewed it, so it does not take on new practice. "
            "Your topics, questions and history stay available to read."
        ),
        code="PREP_COMPLETED",
    )


async def mark_completed(*, user_id: str, prep_id: str) -> Any:
    """Mark a preparation as completed, for a learner finishing one *before* its exam.

    Abandoning a preparation, or deciding they are done with it early. Req 4.10: preserve all data.

    **Refuses once the exam has passed**, and that refusal is the point of this function now. A
    preparation in `AWAITING_REVIEW` is waiting on the one question only the learner can answer, and
    completing it here would set `COMPLETED` with no `PrepOutcome` behind it — restoring the exact claim
    the review exists to remove, through a control that says nothing about the exam. Both clients hide the
    button in that state, but a hidden button is a UI decision and this is an invariant: the only path from
    `AWAITING_REVIEW` to `COMPLETED` is `prep_outcome_service.submit_prep_outcome`.

    A `409` rather than a `422`: nothing about the request is malformed, the preparation is simply in a
    state where this is not the way it finishes.
    """
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)
    if getattr(prep, "status", None) == "AWAITING_REVIEW":
        raise ConflictError(
            "This preparation is waiting for your review",
            detail=(
                "Its exam has passed, so it finishes when you say how it went rather than by being "
                "marked complete. Answer the review to close it."
            ),
            code="PREP_AWAITING_REVIEW",
        )
    result = await repo.update_exam_prep(prep_id, {"status": "COMPLETED"})

    # Record in activity feed
    from . import activity_feed_service

    await activity_feed_service.record(
        user_id=user_id,
        activity_type="preparation_completed",
        title=f"Completed preparation: {prep.subject}",
        entity_type="preparation",
        entity_id=prep_id,
        context={"source": "personal", "prepId": prep_id},
    )

    # Check milestones (first_prep_complete)
    from . import milestone_service

    completed_preps = await repo.list_exam_preps(user_id)
    preps_completed = len([p for p in completed_preps if p.status == "COMPLETED"])
    await milestone_service.check_milestones(user_id, {"preps_completed": preps_completed})

    return result


async def mark_preparations_awaiting_review() -> int:
    """Move preparations whose exam has passed into `AWAITING_REVIEW`, and ask how it went.

    **This used to set `COMPLETED`.** A date-based sweep declared every preparation finished the morning
    after its exam, regardless of readiness — so a learner who was 30 percent ready for an exam they
    missed got a preparation recorded as finished, and it then dropped out of
    `PREP_STATUSES_WORTH_A_GOAL` so it was not even a candidate for a goal any more. The date passing says
    the exam happened. It does not say they sat it, that they were ready, or that it went well; the only
    party who knows is the learner. See `prep_outcome_service`.

    So this now does two things and asserts nothing:

    1. moves the preparation to `AWAITING_REVIEW` — waiting on an answer, which is neither finished nor
       overdue;
    2. asks the learner once, then up to `MAX_REVIEW_REMINDERS` more times, and stops.

    A learner who has declined is excluded by the query. A learner who has answered is already
    `COMPLETED` and never appears.

    Returns the number of preparations moved into the awaiting state, not the number of messages sent —
    the two differ on every run after the first, and the status change is the part that matters.
    """
    from src.domains.progress.services import adaptive_response_metrics

    from . import notification_service, prep_outcome_service

    now = datetime.now(UTC)
    preps = await repo.list_preps_awaiting_review(before=now)

    moved = 0
    for prep in preps:
        try:
            if prep.status != "AWAITING_REVIEW":
                await repo.update_exam_prep(prep.id, {"status": "AWAITING_REVIEW"})
                adaptive_response_metrics.log_ask_event(
                    "review_asked", prep_id=prep.id, from_status=prep.status
                )
                moved += 1

            reminders = prep.review_reminders_sent or 0
            if (
                prep.review_asked_at is not None
                and reminders >= prep_outcome_service.MAX_REVIEW_REMINDERS
            ):
                # Budget spent. The preparation stays in `AWAITING_REVIEW` — an honest record that the
                # exam happened and we do not know how it went — and nothing more is sent.
                #
                # Logged, because this is the moment a learner passes out of reach and nothing else records
                # it: the row looks identical to one still within its budget.
                adaptive_response_metrics.log_ask_event(
                    "review_budget_spent", prep_id=prep.id, reminders=reminders
                )
                continue

            await notification_service.create_notification(
                user_id=prep.user_id,
                type="preparation_review",
                canonical_type="learning.reflection_opportunity",
                title=f"How did {prep.subject} go?",
                body=(
                    "Tell us how it went and how well the preparation served you. "
                    "It takes a moment, and it is what marks this preparation finished."
                ),
                # Above the engagement nudge (2) and the plan check-in (4): this is a question only the
                # learner can answer, and it expires in usefulness as memory of the exam fades.
                priority=3,
                action={"version": 1, "kind": "OPEN_PREPARATION", "entityId": prep.id},
                action_data={
                    "type": "navigate",
                    "prepId": prep.id,
                    "route": "preparation_review",
                },
                idempotency_key=f"preparation-review:{prep.id}:{reminders}",
                source_domain="personal_learning",
                source_entity_type="preparation",
                source_entity_id=prep.id,
            )
            # Recorded whether or not the notification reaches them: quiet hours hold it until morning,
            # the learner's daily allowance can defer it to tomorrow, and one held too long expires
            # rather than arriving stale. Counting only messages that landed would let a held-back ask
            # retry every night, which is how a throttle becomes a backlog that arrives all at once.
            # `run_weekly_check_ins` learned the same lesson.
            await repo.update_exam_prep(
                prep.id,
                {
                    "reviewAskedAt": prep.review_asked_at or now,
                    "reviewRemindersSent": reminders + (1 if prep.review_asked_at else 0),
                },
            )
            if prep.review_asked_at is not None:
                adaptive_response_metrics.log_ask_event(
                    "review_reminded", prep_id=prep.id, reminder=reminders + 1
                )
        except Exception:
            logger.exception("Could not move a preparation into review", extra={"prep_id": prep.id})

    return moved


async def search_question_bank(
    *,
    user_id: str,
    prep_id: str,
    topic_id: str | None = None,
    difficulty: str | None = None,
    source: str | None = None,
    flagged_only: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    """A page of a preparation's question bank.

    Returns listing shapes that **omit the answer key**. The bank is a browsing
    surface, so including `correctAnswer` would let a learner read every answer
    without practising — reopening the leak Decision C closed at quiz start.

    Ownership is checked on the preparation, and the query is scoped to it, so a
    topic or question id from another learner's preparation cannot be reached.
    """
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    rows, total = await repo.search_prep_questions(
        prep_id,
        user_id=user_id,
        topic_id=topic_id,
        difficulty=difficulty,
        source=source,
        flagged_only=flagged_only,
        skip=(page - 1) * page_size,
        take=page_size,
    )

    # One query for the whole page's topic titles. The bank tab groups and labels
    # by topic, and resolving it here is cheaper and less error-prone than a client
    # holding the topic list and joining row by row.
    topic_titles = {topic.id: topic.title for topic in await repo.list_prep_topics(prep_id)}

    items = [
        {
            "id": question.id,
            "prepId": question.prep_id,
            "prepTopicId": question.prep_topic_id,
            "prepTopicTitle": topic_titles.get(question.prep_topic_id),
            "questionText": question.question_text,
            "questionType": question.question_type,
            "options": question.options if isinstance(question.options, list) else None,
            "difficulty": question.difficulty,
            "source": question.source,
            "sourceYear": question.source_year,
            "timesAnswered": question.times_answered or 0,
            "timesCorrect": question.times_correct or 0,
            # None rather than 0 until attempted, so an unpractised question does
            # not read as one the learner always gets wrong.
            "accuracyPercent": (
                round((question.times_correct / question.times_answered) * 100, 1)
                if question.times_answered
                else None
            ),
            "isFlagged": flag is not None,
            "flagNote": flag.note if flag is not None else None,
            "createdAt": question.created_at,
        }
        for question, flag in rows
    ]
    return items, total


async def flag_question(
    *, user_id: str, prep_id: str, question_id: str, note: str | None = None
) -> dict[str, Any]:
    """Flag a banked question for later review.

    Idempotent: flagging an already-flagged question updates the note if one is
    supplied and otherwise changes nothing, so a repeated tap is not an error.

    Scoped twice, as elsewhere: the preparation must belong to the learner, and the
    question must belong to that preparation.
    """
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    question = await repo.find_prep_question(question_id, prep_id)
    if not question:
        raise NotFoundError("PrepQuestion", question_id)

    flag = await repo.upsert_question_flag(user_id=user_id, prep_question_id=question_id, note=note)
    return {
        "questionId": question_id,
        "isFlagged": True,
        "note": flag.note,
        "createdAt": flag.created_at,
    }


async def unflag_question(*, user_id: str, prep_id: str, question_id: str) -> None:
    """Remove a learner's flag from a question.

    Succeeds whether or not a flag was present. Unflagging something already
    unflagged is the outcome the caller wanted, not an error.
    """
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    question = await repo.find_prep_question(question_id, prep_id)
    if not question:
        raise NotFoundError("PrepQuestion", question_id)

    await repo.delete_question_flag(user_id=user_id, prep_question_id=question_id)


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

# The exam itself is always the last thing on the timeline, whatever the plan says.
_EXAM_MILESTONE_KIND = "EXAM"
_STUDY_MILESTONE_KIND = "STUDY"

# A plan replaced by a newer one for the same preparation. Its pending items are no
# longer what to do next, but the ones the learner completed are real work on real
# dates, so they stay on the timeline.
SUPERSEDED_PLAN_STATUS = "SUPERSEDED"


async def generate_preparation_plan(*, user_id: str, prep_id: str) -> Any:
    """Generate the study plan a preparation's timeline is derived from.

    Two preconditions, both refusals rather than degraded output. `check_prep_timeline`
    measured how common each is: of 23 live preparations, 12 have no topics and 12
    have a target date in the past.

    - **No topics.** `generate_plan` falls through to `_generate_topics_from_goal`,
      which asks a model to invent items from the title. That produces a plausible
      schedule for a subject the learner never described, so the plan and their
      material are about different things. Extraction is one action away.
    - **Target date in the past.** `days_available` is `max(1, (deadline - now).days)`,
      so every topic is scheduled today no matter how many hours it adds up to. The
      settings tab can move the date; a plan nobody can follow cannot be fixed later.

    Regenerating supersedes the previous plan instead of adding to it. The timeline
    merges items across plans, so without this a second generation would list every
    topic twice on overlapping days — a duplicated to-do list rather than history.
    """
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    exam_date = prep.exam_date
    if exam_date is not None:
        if exam_date.tzinfo is None:
            exam_date = exam_date.replace(tzinfo=UTC)
        if exam_date < datetime.now(UTC):
            raise MaigieError(
                "This preparation's target date has passed. Move it to a future date "
                "in settings, then generate a plan.",
                status_code=409,
                code="PREP_TARGET_DATE_PASSED",
            )

    topics = await repo.list_prep_topics(prep_id)
    if not topics:
        raise MaigieError(
            "Add topics first — a plan schedules the topics you are preparing, and "
            "this preparation has none yet.",
            status_code=409,
            code="PREP_TOPICS_REQUIRED",
        )
    if all(topic.status == "MASTERED" for topic in topics):
        # Not a gap. There is nothing left to schedule, which is the good outcome.
        raise MaigieError(
            "Every topic here is already mastered, so there is nothing left to "
            "schedule. Practise to keep it, or mark the preparation complete.",
            status_code=409,
            code="PREP_ALL_TOPICS_MASTERED",
        )

    from . import study_plan_service

    # Supersede before creating, so a failure part-way leaves the old plan in place
    # rather than leaving the preparation with none.
    existing = await repo.list_prep_study_plans(prep_id, user_id)

    plan = await study_plan_service.generate_plan(
        user_id=user_id,
        data={
            "title": f"Study Plan — {prep.subject}",
            "deadline": prep.exam_date,
            "prepId": prep_id,
        },
    )

    for previous in existing:
        if previous.id != plan.id and previous.status not in (
            "COMPLETED",
            SUPERSEDED_PLAN_STATUS,
        ):
            await repo.update_plan_status(previous.id, SUPERSEDED_PLAN_STATUS)

    return plan


async def get_timeline(*, user_id: str, prep_id: str) -> dict[str, Any]:
    """A preparation's timeline, derived from its linked study plan.

    **No milestone entity.** A preparation can already generate a study plan whose
    items carry a scheduled date, an estimate, a status, and a topic — which is a
    timeline. Adding a parallel `PrepMilestone` table would create a second answer
    to "what should I do by when", and the two would drift the first time a plan was
    regenerated or an item rescheduled.

    The target date is appended as a final milestone, because it is the one date the
    learner is actually working towards and it belongs on the same axis.
    """
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)

    plans = await repo.list_prep_study_plans(prep_id, user_id)

    milestones: list[dict[str, Any]] = []
    for plan in plans:
        superseded = plan.status == SUPERSEDED_PLAN_STATUS
        for item in plan.items or []:
            # A superseded plan contributes only what was completed. Its pending
            # items were replaced, and listing them alongside the current plan's
            # would schedule the same topic twice on overlapping days.
            if superseded and item.status != "COMPLETED":
                continue
            milestones.append(
                {
                    "id": item.id,
                    "kind": _STUDY_MILESTONE_KIND,
                    "title": item.title,
                    "detail": item.description,
                    "scheduledFor": item.scheduled_date,
                    "estimatedMinutes": item.estimated_minutes,
                    "status": item.status,
                    "itemType": item.item_type,
                    "prepTopicId": item.prep_topic_id,
                    "studyPlanId": plan.id,
                    "completedAt": item.completed_at,
                }
            )

    # Whether there is still a plan to follow, which is what the client offers
    # generation on. A plan that was superseded and had nothing completed leaves
    # no trace, and reporting `True` for it would be the "planned and empty" misread
    # this flag exists to prevent.
    has_current_plan = any(plan.status != SUPERSEDED_PLAN_STATUS for plan in plans) or bool(
        milestones
    )

    milestones.sort(key=lambda milestone: milestone["scheduledFor"])

    # The exam is a milestone too, and the only one guaranteed to exist.
    milestones.append(
        {
            "id": f"exam-{prep.id}",
            "kind": _EXAM_MILESTONE_KIND,
            "title": prep.subject,
            "detail": None,
            "scheduledFor": prep.exam_date,
            "estimatedMinutes": None,
            "status": prep.status,
            "itemType": None,
            "prepTopicId": None,
            "studyPlanId": None,
            "completedAt": None,
        }
    )

    return {
        "preparationId": prep_id,
        # False when no plan has been generated yet. The client uses this to offer
        # plan generation instead of rendering a timeline containing only the exam,
        # which would read as though the work had been planned and found to be empty.
        "hasStudyPlan": has_current_plan,
        "milestones": milestones,
    }
