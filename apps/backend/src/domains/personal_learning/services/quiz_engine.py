"""
Quiz Engine — generates, serves, and scores practice quizzes.

Supports multiple modes: FULL_PRACTICE (all topics), WEAK_AREAS (mastery < 70),
TOPIC_FOCUS (single topic), and QUICK_REVIEW.
"""

import asyncio
import logging
import random
import re
import time
from datetime import UTC, datetime, timezone
from typing import Any

from src.shared.exceptions import MaigieError, NotFoundError

from .. import models
from ..repository import personal_learning_repo as repo
from . import prep_adaptive, prep_material_context

logger = logging.getLogger(__name__)

# A multiple-choice question needs at least two options to be a choice at all.
_MIN_OPTIONS = 2

# Recognised difficulty labels. Anything else the generator returns is dropped
# rather than stored, so the column cannot become a free-text dumping ground.
_DIFFICULTIES = ("EASY", "MEDIUM", "HARD")

# Provenance. Always set by the server: a generator's claim about where its own
# output came from is not evidence.
QUESTION_SOURCE_AI = "AI_GENERATED"
QUESTION_SOURCE_PAST_PAPER = "PAST_PAPER"

# An exam tip is capped rather than trusted to be "one sentence".
_MAX_EXAM_TIP_CHARS = 500

# A hint is capped for the same reason, and more tightly: a long hint is an
# explanation wearing a different hat.
_MAX_HINT_CHARS = 300

# Hint levels a learner can ask for, in order.
HINT_LEVEL_NUDGE = 1
HINT_LEVEL_NARROW = 2
MAX_HINT_LEVEL = HINT_LEVEL_NARROW

# Modes played under examination conditions: no hints, and no feedback of any kind
# until the session is complete.
#
# This is a deliberate, narrow exception to the per-question disclosure boundary,
# not a hole in it. Learning in small steps and rehearsing an exam are opposite
# requirements — one teaches as you go, the other measures you under pressure — and
# a simulation that tells you the answer after every question simulates nothing.
# The guarantee that matters is unchanged: a learner still never sees an answer to a
# question they have not committed to.
EXAM_CONDITION_MODES = ("PAST_PAPER_SIM",)


class GenerationStage:
    """The phases a session passes through while `status` is `GENERATING`.

    Each one is a real server-side phase with a write behind it, which is the whole
    point: Phase 4e refused a timer-driven progress bar because the client cannot
    observe the stages of a synchronous POST, and a bar that guesses would report
    "Writing questions" for a request that had already failed selecting them.

    Ordered. `INDEX` gives the client something to draw without hardcoding the list.
    """

    PREPARING = "PREPARING"
    REUSING_BANK = "REUSING_BANK"
    WRITING_QUESTIONS = "WRITING_QUESTIONS"
    CHECKING_QUESTIONS = "CHECKING_QUESTIONS"
    READY = "READY"

    ORDER = (PREPARING, REUSING_BANK, WRITING_QUESTIONS, CHECKING_QUESTIONS, READY)
    INDEX = {stage: position for position, stage in enumerate(ORDER)}


#: A session still `GENERATING` after this long is treated as lost rather than slow.
#:
#: Generation runs as an in-process background task, so a deploy or a crash between
#: creating the session and finishing it would otherwise leave the row `GENERATING`
#: forever — and the client would poll a spinner with no end. The bound is the
#: provider timeout (60s) plus room for the surrounding database work; the measured
#: p50 is 16.3s, so this is generous rather than tight.
GENERATION_TIMEOUT_SECONDS = 90


def generation_progress(stage: str | None) -> float | None:
    """How far through generation a session is, 0.0-1.0, or `None` if unknown.

    Derived from the stage rather than stored, so it cannot disagree with it. `None`
    for a session that predates the column: no stage was recorded, and reporting 0
    would claim it had not started when in fact it had finished.
    """
    if stage is None or stage not in GenerationStage.INDEX:
        return None
    last = len(GenerationStage.ORDER) - 1
    return round(GenerationStage.INDEX[stage] / last, 2)


def defers_feedback(mode: str | None) -> bool:
    """Whether this mode withholds all feedback until the session completes."""
    return (mode or "").upper() in EXAM_CONDITION_MODES


# A topic at or below this score is reported as a weak area on completion.
_WEAK_AREA_THRESHOLD = 70


async def start_quiz(
    *,
    user_id: str,
    prep_id: str,
    mode: str,
    topic_id: str | None = None,
    question_count: int | None = None,
) -> Any:
    """
    Start a quiz session and generate questions.

    Req 4.5: FULL_PRACTICE — cover all topics
    Req 4.6: WEAK_AREAS — topics with mastery < 70
    Req 4.7: TOPIC_FOCUS — single specified topic
    """
    from . import feature_tier_service
    from .llm_resilient import generate_content_json

    # --- Commercial gate: check mode access ---
    if mode in ("PAST_PAPER_SIM", "ADAPTIVE"):
        cap_result = await feature_tier_service.check_capability(
            user_id, "quiz_modes", requested_value=mode
        )
        if not cap_result.allowed:
            from fastapi import HTTPException

            # Built through the published model rather than an inline dict, so
            # the payload the client renders its upgrade path from cannot drift
            # from the declared 403 schema.
            detail = models.UpgradeRequiredDetail(
                upgrade_required=True,
                reason=cap_result.reason,
                capability=cap_result.capability,
                upgrade_url=cap_result.upgrade_url,
                trial_available=cap_result.trial_available,
                upgrade_value=cap_result.upgrade_value,
            )
            raise HTTPException(status_code=403, detail=detail.model_dump(by_alias=True))
        # Record PLUS feature usage
        await feature_tier_service.get_quality_tier(user_id)  # side-effect: validates tier
        from . import trial_service

        await trial_service.record_plus_feature_used(user_id, "quiz_modes")

    # The preparation, its topics and its materials, in one wave rather than three.
    #
    # A round trip to the hosted database costs ~1.2s, so three sequential reads were
    # most of a 7.9s "returns immediately" response. They are independent: all three
    # are keyed on `prep_id`, and none needs another's result.
    #
    # Scoping is unchanged. Ownership is still decided by `find_exam_prep(prep_id,
    # user_id)` and still raises before anything is returned; the concurrent reads are
    # keyed only by `prep_id` and their results are discarded on that raise, so no
    # other learner's rows can reach a response.
    prep, all_topics, materials = await asyncio.gather(
        repo.find_exam_prep(prep_id, user_id),
        repo.list_prep_topics(prep_id),
        repo.list_prep_materials(prep_id),
    )

    if not prep:
        raise NotFoundError("Preparation", prep_id)

    # No new practice once the exam has happened.
    #
    # Checked **before** the topics guard, so a finished preparation with no topics says "this is
    # finished" rather than "extract topics first" — advice for a step that is itself now refused.
    #
    # Not only tidiness: a quiz writes `QuizSession` rows and moves topic mastery, which feeds
    # `averageMasteryPercent` — the readiness figure the post-exam calibration in §6.2 scores against the
    # recorded outcome. Practising afterwards rewrites the prediction after the result is known.
    from . import exam_prep_service

    exam_prep_service.ensure_accepts_new_work(prep)

    if not all_topics:
        # A bare ValueError here reached clients as a generic 500 via the
        # catch-all handler, hiding an actionable next step.
        raise MaigieError(
            "This preparation has no topics yet. Extract topics before practising.",
            status_code=409,
            code="PREP_TOPICS_REQUIRED",
        )

    if mode == "FULL_PRACTICE":
        target_topics = all_topics
    elif mode == "WEAK_AREAS":
        target_topics = [t for t in all_topics if t.mastery_score < 70.0]
        if not target_topics:
            target_topics = all_topics  # Fallback to all if none are weak
    elif mode == "TOPIC_FOCUS":
        if not topic_id:
            raise MaigieError(
                "A topic is required for topic-focused practice.",
                status_code=400,
                code="PREP_TOPIC_REQUIRED",
            )
        target_topics = [t for t in all_topics if t.id == topic_id]
        if not target_topics:
            raise NotFoundError("PrepTopic", topic_id)
    elif mode in ("ADAPTIVE", "PAST_PAPER_SIM"):
        # Both work across the whole preparation. ADAPTIVE then narrows by
        # competence below; PAST_PAPER_SIM deliberately covers everything, because
        # an exam does not visit only your weak topics.
        target_topics = all_topics
    else:
        # QUICK_REVIEW — mix of topics, fewer questions
        target_topics = all_topics[:5]

    # The learner's choice wins; otherwise the mode and the material decide.
    count = question_count or default_question_count(mode, len(target_topics))

    # Every mode now reads the learner's material, scoped to that learner. Nobody
    # else's documents, and no third-party past papers — which would be someone
    # else's copyright to license, not ours to use.
    #
    # Only PAST_PAPER_SIM used to. The other five sent topic titles and descriptions
    # alone, so their questions came from the model's knowledge of the phrase
    # "Functions and Graphs" rather than from the uploaded document — while the
    # workspace promised questions "tailored to your preparation" and the launcher
    # had a heading reading "Written from your material".
    #
    # The difference between the two cases is strictness, not access: a simulation
    # must not introduce anything absent from the paper, whereas ordinary practice is
    # allowed to ask a fair question the document implies.
    is_exam_simulation = mode == "PAST_PAPER_SIM"
    # Selected from the rows already fetched in the wave above — no further round trip.
    material_context = prep_material_context.select(
        materials,
        budget=(
            prep_material_context.PAST_PAPER_BUDGET
            if is_exam_simulation
            else prep_material_context.QUESTION_GROUNDING_BUDGET
        ),
        # A paper is built from past questions and the syllabus that defines its
        # scope, falling back to everything when neither is labelled.
        categories=("PAST_QUESTION", "SYLLABUS") if is_exam_simulation else None,
    )
    if is_exam_simulation and not material_context.has_text:
        raise MaigieError(
            "Upload some course material first — exam simulation is built from "
            "your own documents.",
            status_code=409,
            code="PREP_MATERIAL_REQUIRED",
        )
    if material_context.has_text:
        logger.info(
            "Quiz generation material context",
            extra={
                "prep_id": prep_id,
                "mode": mode,
                "files_read": len(material_context.excerpts),
                "files_omitted": len(material_context.omitted),
                "stored_chars": material_context.stored_chars,
                "used_chars": material_context.used_chars,
            },
        )

    # ADAPTIVE composes a plan from what practice has revealed: which topics, at
    # which difficulty, ordered to ramp gently. Before this, the mode was billed as
    # a Plus feature and behaved exactly like the free quick-review path.
    adaptive_plan: list[Any] = []
    if mode == "ADAPTIVE":
        adaptive_plan, _competence = await prep_adaptive.load_plan(
            user_id=user_id, topics=target_topics, count=count
        )
        if adaptive_plan:
            planned_ids = {slot.topic_id for slot in adaptive_plan}
            target_topics = [t for t in target_topics if t.id in planned_ids] or target_topics

    # Create the session before generating, so an attempt is never lost, and mark
    # it GENERATING rather than IN_PROGRESS (Decision H). A request that dies
    # mid-generation would otherwise leave a 0-question session sitting in
    # IN_PROGRESS forever, indistinguishable from one that is still working.
    quiz_session = await repo.create_quiz_session(
        {
            "userId": user_id,
            "prepId": prep_id,
            "mode": mode,
            "topicId": topic_id,
            "status": "GENERATING",
            "totalQuestions": count,
            "generationStage": GenerationStage.PREPARING,
        }
    )

    # Everything above is validation and cheap reads, and every failure above is a
    # refusal the learner can act on — no topics, no topic chosen, no readable
    # material, mode not on their plan. Those must stay in the request, because a
    # refusal returned as a *failed session* is much harder to act on than a 4xx.
    #
    # Everything below is the expensive half, and the 16.3s p50 lives in it. It runs
    # in the background so the request can return a session the client can poll,
    # which is what makes a real staged progress display possible at all.
    _schedule_generation(
        quiz_id=quiz_session.id,
        user_id=user_id,
        prep_id=prep_id,
        mode=mode,
        count=count,
        target_topics=target_topics,
        all_topics=all_topics,
        adaptive_plan=adaptive_plan,
        material_context=material_context,
        is_exam_simulation=is_exam_simulation,
    )

    session = await repo.get_quiz_session(quiz_session.id, user_id)
    # No questions yet, and the response says so through `status` and
    # `generationStage` rather than through an empty array the client has to interpret.
    return _build_quiz_response(session, [], [], {topic.id: topic.title for topic in all_topics})


def _schedule_generation(**kwargs: Any) -> None:
    """Run generation outside the request, without requiring a broker.

    An in-process `asyncio` task rather than a Celery job. Celery is available, but
    routing quiz generation through it would make practice depend on a worker being
    up — and `check_prepare_exercised.py` already showed what that costs: readiness
    snapshots have never been written because beat is not running. A learner should
    not lose the ability to practise for the same reason.

    The trade is that a restart mid-generation abandons the task. `get_quiz` handles
    that by treating a session still `GENERATING` past `GENERATION_TIMEOUT_SECONDS`
    as failed, so a lost task surfaces as an actionable error rather than a spinner
    with no end.
    """
    task = asyncio.create_task(_run_generation(**kwargs))
    # Held so the event loop cannot garbage-collect a running task, which is a real
    # asyncio footgun: without a reference the task can vanish mid-await.
    _IN_FLIGHT.add(task)
    task.add_done_callback(_IN_FLIGHT.discard)


#: Strong references to running generation tasks. See `_schedule_generation`.
_IN_FLIGHT: set[Any] = set()


async def _run_generation(
    *,
    quiz_id: str,
    user_id: str,
    prep_id: str,
    mode: str,
    count: int,
    target_topics: list[Any],
    all_topics: list[Any],
    adaptive_plan: list[Any],
    material_context: prep_material_context.MaterialContext,
    is_exam_simulation: bool,
) -> None:
    """The expensive half of starting a quiz, run outside the request.

    Everything here was the tail of `start_quiz`. It is unchanged except that each
    phase now records itself, and that a failure marks the session `FAILED` instead of
    raising to a caller — there is no caller left to raise to, and the client learns
    the outcome by polling `status`.
    """
    from .llm_resilient import generate_content_json

    try:
        # ADAPTIVE draws on the bank before generating anything. This is the first real
        # payoff of promoting questions out of the session that created them: a question
        # written last week at the right difficulty beats a fresh one, because it is
        # already validated and it carries its own answer history. Done *before*
        # generation so we do not pay for questions we would then discard.
        reused = 0
        if adaptive_plan:
            await _set_stage(quiz_id, GenerationStage.REUSING_BANK)
            reused = await _fill_from_bank(
                prep_id=prep_id, quiz_session_id=quiz_id, plan=adaptive_plan
            )

        remaining = max(0, count - reused)

        # Generate questions via LLM.
        #
        # Topics are numbered and the model is asked for the number rather than the
        # title. Attribution used to be a lowercased match on an LLM-returned title,
        # so any paraphrase silently produced `prepTopicId = None`, which broke both
        # the topic breakdown and the per-topic mastery updates that readiness is
        # derived from. Numbering also keeps internal topic ids out of the prompt.
        topics_text = "\n".join(
            f"{index}. {topic.title}: {topic.description or ''}"
            for index, topic in enumerate(target_topics, start=1)
        )
        # Questions are grounded in the learner's own material rather than invented from
        # the topic titles, so they test what the learner was actually given.
        grounding = ""
        if material_context.has_text:
            source_block = material_context.as_prompt_block()
            if is_exam_simulation:
                grounding = (
                    "Base every question strictly on this source material, which the "
                    "learner uploaded themselves. Do not introduce facts that are not "
                    "present in it.\n"
                    f"--- SOURCE MATERIAL ---\n{source_block}\n--- END SOURCE MATERIAL ---\n\n"
                    "Write questions in the style of a written examination: no hints in "
                    "the wording, and a spread of difficulty across the paper.\n\n"
                )
            else:
                # Deliberately weaker than the simulation's instruction. An excerpt is a
                # sample of the document, not the whole of it, so forbidding anything
                # absent would rule out fair questions about material that exists but did
                # not fit the budget. It must still not wander off the subject.
                grounding = (
                    "Draw the questions from this material, which the learner uploaded "
                    "themselves. Use its terminology, notation and worked conventions, "
                    "and stay within the subject it covers. Where it is silent, ask a "
                    "question the material clearly implies rather than inventing new "
                    "facts.\n"
                    f"--- SOURCE MATERIAL ---\n{source_block}\n--- END SOURCE MATERIAL ---\n\n"
                )

        prompt = (
            f"{grounding}"
            f"Generate {remaining} quiz questions for these numbered topics:\n{topics_text}\n\n"
            f"Return a JSON array of question objects with:\n"
            f"- 'topicNumber': the number of the topic this tests, from the list above\n"
            f"- 'questionText': the question\n"
            f"- 'questionType': 'MULTIPLE_CHOICE'\n"
            f"- 'options': array of 4 options (strings)\n"
            f"- 'correctAnswer': the correct option (must match one of the options exactly)\n"
            f"- 'explanation': brief explanation of why the answer is correct\n"
            f"- 'difficulty': one of EASY, MEDIUM, HARD\n"
            f"- 'examTip': one sentence on how to approach this kind of question\n"
            f"- 'hint': one sentence pointing at the concept or method needed, which "
            f"must NOT reveal, restate or paraphrase the correct answer\n\n"
            f"Return ONLY the JSON array."
        )

        # Timed so the sync-versus-queued decision (Decision H) can be revisited from
        # measurements rather than from opinion. The provider is chosen per user by
        # llm_resilient, so this covers whichever of Gemini/OpenAI/Anthropic ran.
        await _set_stage(quiz_id, GenerationStage.WRITING_QUESTIONS)
        started = time.monotonic()
        questions_data: Any = []
        if remaining > 0:
            try:
                questions_data = await generate_content_json(
                    prompt, max_tokens=8000, timeout_s=60, fallback=[], user_id=user_id
                )
            except Exception as e:
                logger.warning(f"Failed to generate quiz questions for prep {prep_id}: {e}")
                questions_data = []
        generation_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "Quiz generation finished",
            extra={
                "prep_id": prep_id,
                "quiz_id": quiz_id,
                "mode": mode,
                "requested": count,
                "generation_ms": generation_ms,
            },
        )

        await _set_stage(quiz_id, GenerationStage.CHECKING_QUESTIONS)

        if not isinstance(questions_data, list):
            logger.warning(
                "Quiz generation returned a non-list payload",
                extra={"prep_id": prep_id, "quiz_id": quiz_id},
            )
            questions_data = []

        # Persist only questions that can actually be scored — see _usable_question.
        # Generation fills whatever the bank could not supply.
        #
        # Normalised in full before anything is written, so answer positions can be
        # balanced across the batch rather than chosen one question at a time. Topic
        # attribution is resolved in the same pass, since it needs the raw candidate.
        created = reused
        rejected = 0
        unattributed = 0
        accepted: list[tuple[dict[str, Any], str | None]] = []
        for candidate in questions_data:
            if reused + len(accepted) >= count:
                break
            normalized = _usable_question(candidate)
            if normalized is None:
                rejected += 1
                continue

            matched_topic_id = _resolve_topic_id(candidate, target_topics)
            if matched_topic_id is None:
                unattributed += 1
            accepted.append((normalized, matched_topic_id))

        # Every position gets used in turn, so no session can land its answers on one
        # letter. Applied to the whole batch, before persisting.
        balance_answer_positions([normalized for normalized, _ in accepted])

        async def _persist(
            normalized: dict[str, Any], matched_topic_id: str | None, order_index: int
        ) -> None:
            # The question is banked against the preparation, then linked to this
            # session at this position. The bank outlives the session, so the question
            # remains browsable and reusable afterwards.
            question = await repo.create_prep_question(
                {
                    "prepId": prep_id,
                    "prepTopicId": matched_topic_id,
                    "questionText": normalized["question_text"],
                    "questionType": normalized["question_type"],
                    "options": normalized["options"],
                    "correctAnswer": normalized["correct_answer"],
                    "explanation": normalized["explanation"],
                    "difficulty": normalized["difficulty"],
                    "examTip": normalized["exam_tip"],
                    "hintNudge": normalized["hint_nudge"],
                    # Set here, not taken from the model. A generator asked to report
                    # its own provenance is not a source of truth about it, and
                    # `sourceYear` stays null because a generated question has no year.
                    "source": QUESTION_SOURCE_AI,
                }
            )
            await repo.attach_question_to_session(
                quiz_session_id=quiz_id,
                prep_question_id=question.id,
                order_index=order_index,
            )

        # Questions are persisted concurrently. Each one still does its own
        # create-then-link in order, because the link needs the new question's id, but
        # the questions do not depend on each other — and a round trip to the hosted
        # database costs ~1.2s, so doing five sequentially spent ~12s of a 70s start on
        # writes alone. Measured before and after with `smoke_generation_stages.py`.
        #
        # `order_index` is computed up front rather than incremented inside the
        # coroutines, so the stored order is the balanced batch order and does not
        # depend on which write finishes first.
        await asyncio.gather(
            *(
                _persist(normalized, matched_topic_id, reused + offset)
                for offset, (normalized, matched_topic_id) in enumerate(accepted)
            )
        )
        created = reused + len(accepted)

        if rejected or unattributed:
            # Unattributed questions are still scorable, but they update no topic's
            # mastery, so they quietly weaken readiness. Worth seeing in logs.
            logger.warning(
                "Generated quiz questions were discarded or unattributed",
                extra={
                    "prep_id": prep_id,
                    "quiz_id": quiz_id,
                    "rejected": rejected,
                    "unattributed": unattributed,
                },
            )

        if created == 0:
            # Decision F: a session with no usable questions is a failure, not a
            # quiz. The row is kept as FAILED so the attempt stays visible for
            # support, and the caller is told, rather than being handed a 201 with an
            # empty `questions` array that no client can render.
            # `generationMs` is written on the failure path too. A start that spent 40s
            # and produced nothing is the most important reading Decision H needs, and
            # recording it only on success would bias the percentile towards the fast
            # attempts.
            await repo.update_quiz_session(
                quiz_id,
                {
                    "status": "FAILED",
                    "totalQuestions": 0,
                    "generationMs": generation_ms,
                    "generationStage": None,
                },
            )
            logger.error(
                "Quiz generation produced no usable questions",
                extra={
                    "prep_id": prep_id,
                    "quiz_id": quiz_id,
                    "mode": mode,
                    "returned": len(questions_data),
                },
            )
            raise MaigieError(
                "We could not generate questions for this practice session. Please try again.",
                status_code=503,
                code="QUIZ_GENERATION_FAILED",
            )

        # Generation succeeded: the session is now playable. Partial generation still
        # makes a usable quiz, so report the real number and give the score an honest
        # denominator.
        await repo.update_quiz_session(
            quiz_id,
            {
                "status": "IN_PROGRESS",
                "totalQuestions": created,
                # Persisted, not just logged. See migration `018`: the p95 that gates
                # Decision H was cited three times and never read, because a log field
                # is not queryable the way every other measurement here was.
                "generationMs": generation_ms,
                "generationStage": GenerationStage.READY,
            },
        )

    except MaigieError:
        # Swallowed on purpose. The only `MaigieError` raised below is the
        # no-usable-questions case, which marks the session `FAILED` and logs before
        # raising — and there is no caller left to receive it. Re-raising would give
        # asyncio an exception nobody retrieves, which is noise, not information. The
        # client learns the outcome from `status`, which is already correct.
        return
    except Exception:
        # Nothing is watching this coroutine, so an unhandled error would vanish and
        # leave the session GENERATING until the staleness bound caught it. Record the
        # failure where the client will actually look.
        logger.exception(
            "Quiz generation failed unexpectedly",
            extra={"quiz_id": quiz_id, "prep_id": prep_id, "mode": mode},
        )
        await repo.update_quiz_session(
            quiz_id, {"status": "FAILED", "totalQuestions": 0, "generationStage": None}
        )


async def _set_stage(quiz_id: str, stage: str) -> None:
    """Record which phase generation reached.

    Best-effort: a failed stage write must not abort a generation that is otherwise
    fine. The learner would rather have their questions than an accurate progress bar.
    """
    try:
        await repo.update_quiz_session(quiz_id, {"generationStage": stage})
    except Exception:  # noqa: BLE001 - progress reporting is not worth failing over
        logger.warning(
            "Could not record generation stage",
            extra={"quiz_id": quiz_id, "stage": stage},
        )


# Reading the question, working it out, and reading the explanation.
MINUTES_PER_QUESTION = 2
# Nothing shorter than this is worth calling a session.
MIN_QUESTION_COUNT = 5
# Two questions a topic is the general rule; each mode then caps it, because what
# makes a session the right length differs by what the session is for.
QUESTIONS_PER_TOPIC = 2
_MODE_QUESTION_CAP: dict[str, int] = {
    # A check-in. Its whole value is being short enough to actually do.
    "QUICK_REVIEW": 10,
    # Targeted work on a few topics, so it needs more than a check-in.
    "WEAK_AREAS": 12,
    # Depth on a single topic. The general rule would give two questions, which is
    # not a drill.
    "TOPIC_FOCUS": 8,
    # An exam section, and the only mode where length is part of the point.
    "PAST_PAPER_SIM": 20,
    # Enough questions to move across the frontier rather than sample one point.
    "ADAPTIVE": 12,
    "FULL_PRACTICE": 20,
}
_DEFAULT_MODE_CAP = 12


def default_question_count(mode: str, target_topic_count: int) -> int:
    """How many questions a session should ask, absent a learner's choice.

    One rule, in one place, because it is used twice: here when generating, and by
    `prep_focus` when recommending — and a recommendation that promises a different
    number from what the session then asks is worse than no recommendation.

    Sized from the material, then capped by mode. `TOPIC_FOCUS` is the reason the
    cap is per mode rather than global: the general two-per-topic rule gives a
    single-topic drill two questions, which is not a drill.
    """
    cap = _MODE_QUESTION_CAP.get((mode or "").upper(), _DEFAULT_MODE_CAP)
    from_material = max(0, target_topic_count) * QUESTIONS_PER_TOPIC
    if from_material <= 0:
        return MIN_QUESTION_COUNT
    return max(MIN_QUESTION_COUNT, min(from_material, cap))


def estimated_minutes(question_count: int) -> int:
    """How long a set of this size takes. Shared, so every surface agrees."""
    return max(MINUTES_PER_QUESTION, question_count * MINUTES_PER_QUESTION)


def balanced_positions(
    total: int, option_count: int, *, rng: random.Random | None = None
) -> list[int]:
    """Assign each of `total` questions a target index for its correct option.

    Every block of `option_count` questions gets a shuffled permutation of all the
    positions, so each position is used exactly once per block. Across a whole
    number of blocks the distribution is therefore **exactly** even, not merely even
    on average, and within any block no position repeats.

    That is the difference from shuffling each question independently: independent
    shuffles are uniform in expectation, but a five-question session can still land
    four answers on A by chance, which is precisely the experience being fixed. A
    real exam paper spreads its answers deliberately; so does this.

    A trailing partial block draws from a fresh permutation, so a remainder never
    repeats a position either.
    """
    generator = rng or random
    positions: list[int] = []
    while len(positions) < total:
        block = list(range(option_count))
        generator.shuffle(block)
        positions.extend(block)
    return positions[:total]


def _move_correct_option(options: list[str], correct_answer: str, target: int) -> list[str]:
    """Reorder `options` so `correct_answer` sits at `target`.

    The other options keep their relative order, which matters when a generator has
    put them in a deliberate sequence (ascending values, chronological events): only
    the answer moves, so a question does not become incoherent to read.

    Safe because `correctAnswer` is stored as the option **text**, not an index or a
    letter, so reordering leaves the key pointing at the same string. Nothing
    downstream reads position — `_check_answer_correctness` resolves letters against
    the stored order at answer time, and the client derives A/B/C/D from the array.
    """
    normalized = [option.strip().lower() for option in options]
    key = correct_answer.strip().lower()
    if key not in normalized:
        # Not answerable; `_usable_question` rejects these, so this is defensive.
        return list(options)

    current = normalized.index(key)
    answer = options[current]
    others = [option for index, option in enumerate(options) if index != current]
    slot = max(0, min(target, len(options) - 1))
    return others[:slot] + [answer] + others[slot:]


def balance_answer_positions(
    questions: list[dict[str, Any]], *, rng: random.Random | None = None
) -> list[dict[str, Any]]:
    """Spread the correct answer's position evenly across a batch of questions.

    Language models have a strong positional bias and the prompt says nothing about
    where the correct option should go. Measured across the banked questions before
    this existed: **A 44%, B 40%, C 15%, D never correct.** A learner guessing A or B
    scored about 85% while knowing nothing, and D was discardable on sight — so the
    score measured position rather than knowledge.

    Asking the model to randomise instead would not fix it: the bias *is* the model
    answering that request, and compliance cannot be verified from the output. Doing
    it here is verifiable and free.

    Questions are grouped by option count so each group is balanced exactly; a
    question with three options cannot be given a fourth position, and mixing the
    counts into one cycle would skew both.

    `TRUE_FALSE` is left alone: the conventional True/False order is worth keeping,
    and with two options there is nothing to exploit beyond a coin flip.
    """
    by_count: dict[int, list[dict[str, Any]]] = {}
    for question in questions:
        options = question.get("options")
        if (
            question.get("question_type") != "MULTIPLE_CHOICE"
            or not isinstance(options, list)
            or len(options) < _MIN_OPTIONS
        ):
            continue
        by_count.setdefault(len(options), []).append(question)

    for option_count, group in by_count.items():
        targets = balanced_positions(len(group), option_count, rng=rng)
        for question, target in zip(group, targets):
            question["options"] = _move_correct_option(
                question["options"], question["correct_answer"], target
            )

    return questions


#: Characters models substitute freely for their ASCII equivalents. A question whose
#: key uses a unicode minus while its option uses a hyphen is a formatting difference,
#: not an unanswerable question.
_EQUIVALENT_CHARS = str.maketrans(
    {
        "\u2212": "-",  # minus sign
        "\u2013": "-",  # en dash
        "\u2014": "-",  # em dash
        "\u00a0": " ",  # non-breaking space
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u00d7": "*",  # multiplication sign
    }
)

#: Stripping a leading enumeration label (`A) `, `(a) `, `1. `) was tried and removed.
#: Any pattern loose enough to catch them also catches real content: `n - 1` reads as
#: label `n`, separator `-`, value `1`, so it normalised to `1` and stopped matching
#: itself. On mathematical material that is most of the option set. Presentation-only
#: normalisation has to be transformations that cannot change meaning, and this one can.


def _comparable(value: str) -> str:
    """A form in which two renderings of the same answer compare equal.

    Deliberately narrow. This normalises **presentation** — case, whitespace, unicode
    look-alikes, LaTeX delimiters, an enumeration label, a trailing full stop — and
    nothing about meaning. It is used only to match the model's own `correctAnswer`
    to the model's own `options` at ingest.

    It is emphatically **not** used to grade a learner. Phase 4d removed a substring
    fallback from grading that marked wrong multiple-choice answers correct; loosening
    that again would repeat the defect. The distinction is that here both strings come
    from the same generator and are meant to be the same string, whereas there one
    string came from the learner and being generous told them they knew something they
    did not.
    """
    text = value.translate(_EQUIVALENT_CHARS)
    text = text.strip().strip("$").strip()
    text = text.rstrip(".").strip()
    return " ".join(text.lower().split())


def _resolve_correct_option(options: list[str], correct_answer: str) -> str | None:
    """Which option the model meant by `correct_answer`, or `None` if unclear.

    Exact match first, so nothing changes for the questions that were already fine.
    Otherwise the presentational normalisation above, and **only if exactly one option
    matches** — an ambiguous match is genuinely unscorable and is still rejected.

    Kept deliberately narrow: case, surrounding whitespace, unicode look-alikes, LaTeX
    `$` delimiters and a trailing full stop. Enumeration labels are *not* stripped;
    see the note by `_comparable`.

    This exists because of a measured regression. Grounding ordinary practice in the
    learner's material (Phase 4m) told the model to use "its terminology, notation and
    worked conventions", and on mathematical material that means notation: a live
    session asked for five questions, the model returned five usable ones, and **four
    were discarded** because the key rendered `x = -3` where the option rendered
    `x = −3`. The rejection rule was right in principle and was throwing away good
    questions in practice.
    """
    for option in options:
        if option.lower() == correct_answer.lower():
            return option

    key = _comparable(correct_answer)
    if not key:
        return None
    matches = [option for option in options if _comparable(option) == key]
    # Exactly one. Two options that normalise alike means the question cannot be
    # scored no matter which is stored, so rejecting is the honest outcome.
    return matches[0] if len(matches) == 1 else None


def _usable_question(candidate: Any) -> dict[str, Any] | None:
    """Normalize one generated question, or return `None` if it cannot be scored.

    Rejecting is better than repairing here, because each rejected case produces
    a score the learner would be right to dispute:

    - No `questionText`: nothing to ask.
    - Empty `correctAnswer`: the column is NOT NULL and this previously
      defaulted to `""`, so the question could never be answered correctly and
      counted against the learner regardless of what they picked.
    - Multiple choice whose `correctAnswer` is not one of its own `options`: the
      right answer is not on offer, so the question can only ever be wrong.
    """
    if not isinstance(candidate, dict):
        return None

    question_text = str(candidate.get("questionText") or "").strip()
    correct_answer = str(candidate.get("correctAnswer") or "").strip()
    if not question_text or not correct_answer:
        return None

    question_type = str(candidate.get("questionType") or "MULTIPLE_CHOICE").strip()

    raw_options = candidate.get("options")
    options: list[str] | None = None
    if isinstance(raw_options, list):
        cleaned = [str(option).strip() for option in raw_options if str(option).strip()]
        options = cleaned or None

    if question_type == "MULTIPLE_CHOICE":
        if not options or len(options) < _MIN_OPTIONS:
            return None
        resolved = _resolve_correct_option(options, correct_answer)
        if resolved is None:
            return None
        # Snapped to the option's exact text. Everything downstream compares the
        # stored key to the stored options, so a key that merely *resembles* an
        # option would fail at grading time even though the question is fine.
        correct_answer = resolved
        # Answer position is *not* fixed here. It is assigned across the whole batch
        # by `balance_answer_positions`, because balancing one question at a time can
        # only be even on average — and a five-question session that lands four
        # answers on A by chance is the experience this exists to prevent.

    explanation = candidate.get("explanation")

    # Metadata is normalized, never trusted. An unrecognised difficulty becomes
    # None rather than being stored, because a badge reading "quite hard" or
    # "Level 4" is worse than no badge: the client would have to render it.
    difficulty = str(candidate.get("difficulty") or "").strip().upper()
    if difficulty not in _DIFFICULTIES:
        difficulty = None

    exam_tip = candidate.get("examTip")
    exam_tip = str(exam_tip).strip()[:_MAX_EXAM_TIP_CHARS] or None if exam_tip else None

    hint = _usable_hint(candidate.get("hint"), correct_answer=correct_answer)

    return {
        "question_text": question_text,
        "question_type": question_type,
        "options": options,
        "correct_answer": correct_answer,
        "explanation": str(explanation).strip() or None if explanation is not None else None,
        "difficulty": difficulty,
        "exam_tip": exam_tip,
        "hint_nudge": hint,
    }


def _usable_hint(raw: Any, *, correct_answer: str) -> str | None:
    """Normalize a hint, or drop it if it gives the answer away.

    A hint that contains the correct answer is not a hint, it is the answer key
    with a different label — and it would defeat withholding the key at all. Asking
    the model not to do it is not the same as it not doing it, so this checks.

    Dropped rather than repaired: no hint is a perfectly acceptable state, and
    editing a model's hint to remove the answer risks leaving a sentence that still
    implies it.
    """
    if not raw:
        return None

    hint = str(raw).strip()[:_MAX_HINT_CHARS].strip()
    if not hint:
        return None

    answer = correct_answer.strip().lower()
    if answer and answer in hint.lower():
        logger.warning("Discarded a generated hint containing the correct answer")
        return None

    return hint


def _resolve_topic_id(candidate: Any, target_topics: list[Any]) -> str | None:
    """Attribute a generated question to one of the requested topics.

    Tried in order of reliability: the topic number the prompt asked for, then a
    title match for a model that ignored the instruction. With a single target
    topic the attribution is unambiguous regardless of what came back.

    Returns `None` when the question cannot be attributed. Such a question is
    still scorable but updates no topic's mastery, so callers should count them.
    """
    if not target_topics:
        return None
    if len(target_topics) == 1:
        return target_topics[0].id
    if not isinstance(candidate, dict):
        return None

    try:
        index = int(candidate.get("topicNumber")) - 1
    except (TypeError, ValueError):
        index = -1
    if 0 <= index < len(target_topics):
        return target_topics[index].id

    title = str(candidate.get("topicTitle") or "").strip().lower()
    if title:
        for topic in target_topics:
            if topic.title.strip().lower() == title:
                return topic.id

    return None


async def submit_answer(*, user_id: str, quiz_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """
    Submit an answer to a quiz question.

    Req 4.8: Evaluate correctness, track time, update topic mastery.

    Ownership is checked twice: the session must belong to `user_id`, and the
    question must belong to that session. Both matter, because the response
    discloses the answer key for the question submitted.
    """
    import asyncio

    # Accept both snake_case (from model_dump) and camelCase (defensive)
    question_id = data.get("question_id") or data.get("questionId")
    user_answer = data.get("user_answer") or data.get("userAnswer")
    time_taken = data.get("time_taken_seconds") or data.get("timeTakenSeconds")

    if not question_id or user_answer is None:
        # Unreachable through the route, whose request model requires both, but
        # a bare ValueError here would surface as a 500 rather than a 400.
        raise MaigieError(
            "questionId and userAnswer are required.",
            status_code=400,
            code="QUIZ_ANSWER_INVALID",
        )

    # Three independent reads, issued together rather than one after another.
    # Each round trip to a hosted database costs real time, and the learner is
    # sitting in front of a "Check answer" button waiting for all of them.
    #
    # They are still *validated* in the original order below, so the response for
    # a bad session, a bad question, or an unauthorised one is unchanged. The
    # question and answer lookups are already scoped by `quiz_id`, so issuing them
    # before the ownership check discloses nothing: their results are discarded if
    # the session check fails.
    quiz, question, existing, link = await asyncio.gather(
        repo.get_quiz_session(quiz_id, user_id),
        repo.find_quiz_question(question_id, quiz_id),
        repo.find_quiz_answer(quiz_id, question_id),
        # Only needed for the observation's hint count, but it depends on nothing,
        # so it rides along here rather than adding a round trip after scoring.
        repo.find_session_question_link(quiz_session_id=quiz_id, prep_question_id=question_id),
    )

    if not quiz:
        raise NotFoundError("QuizSession", quiz_id)

    if quiz.status == "COMPLETED":
        raise MaigieError(
            "This practice session is already complete.",
            status_code=409,
            code="QUIZ_ALREADY_COMPLETED",
        )
    if quiz.status == "GENERATING":
        raise MaigieError(
            "This practice session is still being prepared. Try again in a moment.",
            status_code=409,
            code="QUIZ_GENERATING",
        )
    if quiz.status == "FAILED":
        raise MaigieError(
            "This practice session could not be generated, so it cannot be answered.",
            status_code=409,
            code="QUIZ_GENERATION_FAILED",
        )

    # Scoped to this session. The lookup is by question id *and* session id, so a
    # question id from another learner's session cannot be answered here and have
    # its answer key read back out of the response.
    if not question:
        raise NotFoundError("PrepQuestion", question_id)

    # Answering is idempotent. Resubmitting replays the stored result instead of
    # scoring again, so the key disclosed by the first submission cannot be fed
    # back to raise the score, and a client retry is harmless.
    if existing:
        return _answer_result(
            question_id=question_id,
            question=question,
            is_correct=existing.is_correct,
            mode=quiz.mode,
            already_answered=True,
        )

    is_correct = _check_answer_correctness(
        user_answer=user_answer,
        correct_answer=question.correct_answer,
        options=question.options,
        question_type=question.question_type,
    )

    # Record the answer (critical — must persist before responding)
    await repo.create_quiz_answer(
        {
            "quizSessionId": quiz_id,
            "questionId": question_id,
            "userAnswer": user_answer,
            "isCorrect": is_correct,
            "timeTakenSeconds": time_taken,
        }
    )

    # Bookkeeping the answer row implies but does not depend on. None of these
    # three needs to see the others, so they go together rather than in series —
    # four sequential round trips became two, which the learner feels directly.
    #
    # They are still awaited, so everything is durable before the response. The
    # cheaper-looking option, backgrounding them, would report a result the
    # database had not finished recording.
    await asyncio.gather(
        # Recomputed from persisted answers rather than incremented, so the count
        # cannot drift from the answers or exceed the number of questions asked.
        repo.sync_quiz_correct_count(quiz_id),
        # Lifetime statistics on the banked question, across every session that has
        # ever asked it. Incremented in SQL so concurrent answers cannot lose a count.
        repo.record_question_attempt(question_id, correct=is_correct),
        # Keep the evidence, not just the verdict, so a conclusion about a learner
        # can be revisited later rather than being a number with no reasoning
        # behind it.
        _record_observation(
            user_id=user_id,
            quiz=quiz,
            question=question,
            is_correct=is_correct,
            time_taken=time_taken,
            hint_count=(link.hint_count or 0) if link is not None else 0,
        ),
    )

    # Update topic mastery — fire-and-forget to avoid blocking the response.
    # Any failure is logged; user experience is not affected.
    #
    # Started *after* the gather above, because the mastery estimate reads
    # observations: the newest answer has to be visible to it, or every estimate
    # lags by one question.
    if question.prep_topic_id:
        asyncio.create_task(_update_topic_mastery_safe(question.prep_topic_id, user_id=user_id))

    return _answer_result(
        question_id=question_id,
        question=question,
        is_correct=is_correct,
        mode=quiz.mode,
        already_answered=False,
    )


def _answer_result(
    *,
    question_id: str,
    question: Any,
    is_correct: bool,
    mode: str | None,
    already_answered: bool,
) -> dict[str, Any]:
    """Build the answer result, respecting examination conditions.

    Under `PAST_PAPER_SIM` the answer is recorded but nothing is disclosed — not the
    key, not the explanation, not even whether it was right. A simulation that marks
    each question as you go is not simulating an exam.
    """
    if defers_feedback(mode):
        return {
            "questionId": question_id,
            "isCorrect": None,
            "correctAnswer": None,
            "explanation": None,
            "alreadyAnswered": already_answered,
            "feedbackDeferred": True,
        }

    return {
        "questionId": question_id,
        "isCorrect": is_correct,
        "correctAnswer": question.correct_answer,
        "explanation": question.explanation,
        "alreadyAnswered": already_answered,
        "feedbackDeferred": False,
    }


async def _fill_from_bank(*, prep_id: str, quiz_session_id: str, plan: list[Any]) -> int:
    """Satisfy as many planned slots as possible from the existing bank.

    Returns how many questions were attached. Each banked question is used at most
    once per session, which the session link's unique constraint would enforce
    anyway — this just avoids relying on an error for control flow.

    A slot the bank cannot fill is left for generation rather than substituted with
    the wrong difficulty: asking a HARD question because no MEDIUM one exists would
    quietly defeat the point of planning.
    """
    used: list[str] = []

    for slot in plan:
        candidates = await repo.list_bank_questions_for_reuse(
            prep_id=prep_id,
            topic_id=slot.topic_id,
            difficulty=slot.difficulty,
            exclude_ids=used,
            take=1,
        )
        if not candidates:
            continue

        question = candidates[0]
        await repo.attach_question_to_session(
            quiz_session_id=quiz_session_id,
            prep_question_id=question.id,
            order_index=len(used),
        )
        used.append(question.id)

    if used:
        logger.info(
            "Adaptive session reused banked questions",
            extra={
                "prep_id": prep_id,
                "quiz_id": quiz_session_id,
                "reused": len(used),
                "planned": len(plan),
            },
        )
    return len(used)


async def _record_observation(
    *,
    user_id: str,
    quiz: Any,
    question: Any,
    is_correct: bool,
    time_taken: Any,
    hint_count: int = 0,
) -> None:
    """Append what this answer revealed. Failure must not fail the answer.

    An observation is valuable but it is not the learner's score. If writing it
    fails, the answer has still been recorded and the session continues; losing one
    row of evidence is a far smaller harm than rejecting a submitted answer.

    `hint_count` is passed in rather than looked up here, so this is a single
    statement: the caller already reads the session link alongside its other
    lookups, and a second trip for a number it has in hand is latency the learner
    waits through.
    """
    try:
        response_ms: int | None = None
        if time_taken is not None:
            try:
                response_ms = max(0, int(time_taken) * 1000)
            except (TypeError, ValueError):
                response_ms = None

        await repo.record_practice_observation(
            {
                "userId": user_id,
                "prepId": getattr(question, "prep_id", None) or quiz.prep_id,
                "prepTopicId": question.prep_topic_id,
                "prepQuestionId": question.id,
                "quizSessionId": quiz.id,
                "isCorrect": is_correct,
                "responseMs": response_ms,
                "hintUsed": hint_count > 0,
                "hintCount": hint_count,
                # Copied now, because difficulty may be recalibrated later and this
                # observation should record what was true at the time.
                "difficulty": getattr(question, "difficulty", None),
                "observedAt": datetime.now(UTC),
            }
        )
    except Exception:
        logger.exception(
            "Failed to record practice observation",
            extra={"quiz_id": quiz.id, "question_id": question.id},
        )


async def request_hint(
    *, user_id: str, quiz_id: str, question_id: str, level: int = HINT_LEVEL_NUDGE
) -> dict[str, Any]:
    """Give the learner a hint, because they asked for one.

    Hints are pulled, never pushed:

        An answer given too quickly prevents learning.
        A hint at the right moment creates breakthrough.
        -- content/intelligence/ch22-the-nature-of-intelligence.mdx

    Two levels. `NUDGE` points at the concept. `NARROW` additionally eliminates one
    wrong multiple-choice option — still a real choice, and computed
    deterministically so asking twice gives the same answer rather than gradually
    eliminating everything.

    Taking a hint is recorded, and it is **not** a penalty. It marks the question as
    sitting at the edge of what the learner can currently do, which is the most
    useful thing practice can tell us and where the next question should aim.
    """
    quiz = await repo.get_quiz_session(quiz_id, user_id)
    if not quiz:
        raise NotFoundError("QuizSession", quiz_id)

    if quiz.status != "IN_PROGRESS":
        raise MaigieError(
            "Hints are only available while a practice session is in progress.",
            status_code=409,
            code="QUIZ_NOT_IN_PROGRESS",
        )

    if defers_feedback(quiz.mode):
        raise MaigieError(
            "This is an exam simulation — hints are not available.",
            status_code=409,
            code="QUIZ_EXAM_CONDITIONS",
        )

    # Scoped through the session link, exactly as answering is.
    question = await repo.find_quiz_question(question_id, quiz_id)
    if not question:
        raise NotFoundError("PrepQuestion", question_id)

    # A hint after answering is pointless: the key has already been disclosed. More
    # importantly, allowing it would let hint counts be run up after the fact,
    # corrupting the signal rather than recording it.
    existing = await repo.find_quiz_answer(quiz_id, question_id)
    if existing:
        raise MaigieError(
            "This question has already been answered.",
            status_code=409,
            code="QUESTION_ALREADY_ANSWERED",
        )

    requested = max(HINT_LEVEL_NUDGE, min(int(level), MAX_HINT_LEVEL))

    nudge = getattr(question, "hint_nudge", None)
    eliminated: str | None = None
    if requested >= HINT_LEVEL_NARROW:
        eliminated = _eliminable_option(question)
    elif not nudge:
        # A level-1 request on a question with no nudge used to return
        # `hintAvailable: false` and still count the request — telling the learner
        # nothing could be offered while level 2 would have offered something, and
        # charging them a hint to find out. Questions generated before `hintNudge`
        # existed are all in this position.
        #
        # Eliminating an option is a stronger hint than a nudge, so this is not the
        # first choice; it is simply better than nothing, and it is still not the
        # answer.
        eliminated = _eliminable_option(question)

    # Counted once per request, in SQL, so concurrent requests cannot both read the
    # same starting value.
    hint_count = await repo.increment_session_question_hints(
        quiz_session_id=quiz_id, prep_question_id=question_id
    )

    return {
        "questionId": question_id,
        "level": requested,
        "nudge": nudge,
        "eliminatedOption": eliminated,
        "hintCount": hint_count,
        # Honest about having nothing useful, rather than returning a hint-shaped
        # object with no hint in it.
        "hintAvailable": bool(nudge) or eliminated is not None,
    }


def _eliminable_option(question: Any) -> str | None:
    """One wrong multiple-choice option, chosen deterministically.

    Deterministic so that repeated requests eliminate the *same* option rather than
    working through them all — otherwise level 2 becomes "show me the answer" after
    enough taps.

    Returns `None` when eliminating would leave no real choice, which is the case
    for short answers and for two-option questions.
    """
    options = question.options if isinstance(question.options, list) else None
    if not options or len(options) <= 2:
        return None

    answer = (question.correct_answer or "").strip().lower()
    for option in options:
        if str(option).strip().lower() != answer:
            return str(option)
    return None


async def _update_topic_mastery_safe(topic_id: str, *, user_id: str) -> None:
    """Wrap _update_topic_mastery with error handling for fire-and-forget use."""
    try:
        await _update_topic_mastery(topic_id, user_id=user_id)
    except Exception as e:
        logger.warning(f"Background mastery update failed for topic {topic_id}: {e}")


async def complete_quiz(
    *, user_id: str, quiz_id: str, duration_seconds: int | None = None
) -> dict[str, Any]:
    """
    Complete a quiz session.

    Req 4.9: Return score summary with per-topic breakdown and weak areas.
    """
    quiz = await repo.get_quiz_session(quiz_id, user_id)
    if not quiz:
        raise NotFoundError("QuizSession", quiz_id)

    if quiz.status == "FAILED":
        raise MaigieError(
            "This practice session could not be generated, so it cannot be completed.",
            status_code=409,
            code="QUIZ_GENERATION_FAILED",
        )
    if quiz.status == "GENERATING":
        raise MaigieError(
            "This practice session is still being prepared.",
            status_code=409,
            code="QUIZ_GENERATING",
        )

    answers = await repo.list_quiz_answers(quiz_id)

    if quiz.status == "COMPLETED":
        # Completing twice must not re-record the activity or re-check
        # milestones, so an already-completed session returns its stored result.
        topic_breakdown = await _compute_topic_breakdown(quiz_id, answers)
        weak_areas = [
            t["title"] for t in topic_breakdown if t.get("score", 0) < _WEAK_AREA_THRESHOLD
        ]
        return {
            "quizId": quiz_id,
            "totalQuestions": quiz.total_questions,
            "correctCount": quiz.correct_count,
            "scorePercentage": quiz.score_percentage or 0.0,
            "topicBreakdown": topic_breakdown,
            "weakAreas": weak_areas,
            "suggestedNextStep": _suggest_next_step(weak_areas),
        }

    now = datetime.now(UTC)
    total = quiz.total_questions or 0
    # Derived from persisted answers, then clamped to the questions actually
    # asked. The denominator is no longer forced to 1 for a question-less
    # session, which reported a real-looking 0% for a quiz that asked nothing.
    correct = await repo.count_correct_quiz_answers(quiz_id)
    if total > 0:
        correct = min(correct, total)
    score_pct = (correct / total) * 100 if total > 0 else 0.0

    # Compute duration server-side if the client did not provide one.
    if duration_seconds is None and quiz.created_at:
        started_at = quiz.created_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        duration_seconds = int((now - started_at).total_seconds())

    # Update quiz session. `correctCount` is written from the recomputed value so
    # the persisted score and the returned summary cannot disagree.
    await repo.update_quiz_session(
        quiz_id,
        {
            "status": "COMPLETED",
            "correctCount": correct,
            "scorePercentage": round(score_pct, 1),
            "durationSeconds": duration_seconds,
            "completedAt": now,
        },
    )

    # Compute per-topic breakdown
    topic_breakdown = await _compute_topic_breakdown(quiz_id, answers)
    weak_areas = [t["title"] for t in topic_breakdown if t.get("score", 0) < _WEAK_AREA_THRESHOLD]

    # Record in activity feed
    from . import activity_feed_service

    await activity_feed_service.record(
        user_id=user_id,
        activity_type="quiz_completed",
        title=f"Completed quiz — {round(score_pct, 1)}% ({correct}/{total})",
        # The preparation, not the session. A finished quiz session has no page on either client, so
        # recording its id in the field that exists to make the entry clickable sent web and mobile to
        # `/preparations/{quizSessionId}` and got a 404. `quizId` stays in the context, and `prepId` is
        # already loaded on `quiz` — no extra read.
        entity_type="preparation",
        entity_id=quiz.prep_id,
        context={
            "source": "personal",
            "quizId": quiz_id,
            "prepId": quiz.prep_id,
            "score": round(score_pct, 1),
        },
    )

    # Check milestones (quiz_90_plus)
    from . import milestone_service

    await milestone_service.check_milestones(user_id, {"quiz_score": round(score_pct, 1)})

    return {
        "quizId": quiz_id,
        "totalQuestions": total,
        "correctCount": correct,
        "scorePercentage": round(score_pct, 1),
        "topicBreakdown": topic_breakdown,
        "weakAreas": weak_areas,
        "suggestedNextStep": _suggest_next_step(weak_areas),
    }


async def get_quiz(*, user_id: str, quiz_id: str) -> Any:
    """Get a quiz session with its questions.

    This is also the polling endpoint while a session is `GENERATING`, so it is where
    a lost generation has to be caught: the background task lives in the API process,
    and a restart between creating the session and finishing it would otherwise leave
    the row `GENERATING` forever with the client polling a spinner that never ends.
    """
    quiz = await repo.get_quiz_session(quiz_id, user_id)
    if not quiz:
        raise NotFoundError("QuizSession", quiz_id)

    quiz = await _fail_if_generation_was_lost(quiz, user_id=user_id)

    questions = await repo.list_quiz_questions(quiz_id)
    answers = await repo.list_quiz_answers(quiz_id)
    topics = await repo.list_prep_topics(quiz.prep_id)
    return _build_quiz_response(
        quiz, questions, answers, {topic.id: topic.title for topic in topics}
    )


async def _fail_if_generation_was_lost(quiz: Any, *, user_id: str) -> Any:
    """Mark a session `FAILED` once it has been `GENERATING` for too long.

    "Too long" is decided against the clock rather than against a heartbeat, which is
    the honest bound available: nothing is watching an abandoned task, so its absence
    cannot be observed directly. `GENERATION_TIMEOUT_SECONDS` is the provider timeout
    plus room, and the measured p50 is 16.3s, so a session that trips this is stuck
    rather than slow.

    Written back rather than merely reported, so the next poll and every later read
    agree, and so the row stops looking like work in progress to the aggregates.
    """
    if quiz.status != "GENERATING":
        return quiz

    created_at = quiz.created_at
    if created_at is None:
        return quiz
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)

    age = (datetime.now(UTC) - created_at).total_seconds()
    if age <= GENERATION_TIMEOUT_SECONDS:
        return quiz

    logger.warning(
        "Quiz generation abandoned; marking the session failed",
        extra={
            "quiz_id": quiz.id,
            "age_seconds": int(age),
            "last_stage": quiz.generation_stage,
        },
    )
    await repo.update_quiz_session(quiz.id, {"status": "FAILED", "generationStage": None})
    return await repo.get_quiz_session(quiz.id, user_id) or quiz


async def list_prep_quizzes(*, user_id: str, prep_id: str) -> list[Any]:
    """List all quiz sessions for a preparation, newest first."""
    prep = await repo.find_exam_prep(prep_id, user_id)
    if not prep:
        raise NotFoundError("Preparation", prep_id)
    quizzes = await repo.list_prep_quizzes(prep_id, user_id)
    return [_build_quiz_response(q, [], []) for q in quizzes]


def _build_quiz_response(
    quiz: Any,
    questions: list[Any],
    answers: list[Any],
    topic_titles: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the quiz session response including questions and the learner's answers.

    The answer key is disclosed **per question, as soon as that question has been
    answered** — teaching in small steps is the point of practice, so the learner
    keeps the explanation for what they have already attempted, including after
    navigating back or resuming the session later.

    What is withheld is the key for questions not yet attempted (Decision C). A
    completed session reveals everything, including questions left unanswered, so
    review is complete.

    The distinction that matters is *answered*, not *in progress*: a client can
    never see the answer to a question the learner has not yet committed to.
    """
    # Index answers by question_id for O(1) lookup
    answer_map = {a.question_id: a for a in answers}
    session_completed = quiz.status == "COMPLETED"
    # Under examination conditions, answering a question earns no disclosure. Review
    # happens once, at the end. Everywhere else, answering reveals that question.
    reveal_on_answer = not defers_feedback(quiz.mode)
    # `questions` is a list of (question, link) pairs. Everything session-specific
    # — position, hints taken — belongs to the link, not to the banked question.
    ordered = list(questions)
    topic_titles = topic_titles or {}

    return {
        "id": quiz.id,
        "user_id": quiz.user_id,
        "prep_id": quiz.prep_id,
        "mode": quiz.mode,
        "topic_id": quiz.topic_id,
        "status": quiz.status,
        "total_questions": quiz.total_questions,
        "correct_count": quiz.correct_count,
        "score_percentage": quiz.score_percentage,
        "duration_seconds": quiz.duration_seconds,
        "completed_at": quiz.completed_at,
        "created_at": quiz.created_at,
        # Which phase generation reached, and how far through that is. Both are
        # `None` once a session is playable, so a client showing a wait screen has a
        # single unambiguous signal to stop: `status` leaves `GENERATING`.
        "generation_stage": quiz.generation_stage,
        "generation_progress": generation_progress(quiz.generation_stage),
        "questions": [
            _question_dict(
                question,
                answer_map.get(question.id),
                order_index=link.order_index,
                hints_used=getattr(link, "hint_count", 0) or 0,
                topic_title=topic_titles.get(question.prep_topic_id),
                reveal_answers=session_completed
                or (reveal_on_answer and question.id in answer_map),
            )
            for question, link in ordered
        ],
    }


def _question_dict(
    question: Any,
    answer: Any | None,
    *,
    order_index: int,
    hints_used: int,
    reveal_answers: bool,
    topic_title: str | None = None,
) -> dict[str, Any]:
    """Serialize a question with the learner's answer attached, if they have one.

    `reveal_answers` gates the answer key. It is not defaulted, so a new caller
    has to state which side of the disclosure boundary it is on rather than
    silently inheriting the leaky behaviour.
    """
    return {
        "id": question.id,
        "question_text": question.question_text,
        "question_type": question.question_type,
        "options": question.options if isinstance(question.options, list) else None,
        "order_index": order_index,
        "prep_topic_id": question.prep_topic_id,
        # Resolved server-side so a runner can label a question without holding the
        # preparation's whole topic list.
        "prep_topic_title": topic_title,
        # Shown from the start: difficulty describes the question, not the answer.
        "difficulty": getattr(question, "difficulty", None),
        # Provenance, also safe before answering: knowing a question came from a
        # 2025 paper reveals nothing about which option is correct. Server-set.
        "source": getattr(question, "source", None),
        "source_year": getattr(question, "source_year", None),
        # So a resumed session can show what the learner already took, rather than
        # silently offering a fresh hint they have effectively already had.
        "hints_used": hints_used,
        "correct_answer": question.correct_answer if reveal_answers else None,
        "explanation": question.explanation if reveal_answers else None,
        # Withheld with the key: a tip about this question can hint at its answer.
        "exam_tip": getattr(question, "exam_tip", None) if reveal_answers else None,
        "user_answer": answer.user_answer if answer else None,
        "is_correct": answer.is_correct if answer else None,
        "time_taken_seconds": answer.time_taken_seconds if answer else None,
        "answered_at": answer.created_at if answer else None,
    }


async def _update_topic_mastery(topic_id: str, *, user_id: str) -> None:
    """Recompute a topic's mastery from the competence model.

    Phase B. This used to be `correct / total` over every answer ever recorded,
    which the book rules out twice: it never forgot a bad week, and it let a single
    mistake stand as the whole assessment.

    `PrepTopic.mastery_score` is now a **cache of the model's `retention`** rather
    than a truth of its own. Everything downstream — readiness, the Learn card, the
    dashboard aggregates — keeps reading the same column and simply gets a better
    number, so no consumer had to change.

    A topic with too little evidence is left **untouched** rather than written to
    zero. Writing zero would assert that the learner knows nothing, which is a claim
    three answers do not support.
    """
    from . import prep_competence

    competence = await prep_competence.load_for_topic(user_id=user_id, topic_id=topic_id)

    if not competence.is_measurable or competence.retention is None:
        logger.debug(
            "Topic mastery left unchanged: not enough evidence",
            extra={
                "topic_id": topic_id,
                "observations": competence.observations,
                "effective_weight": competence.effective_weight,
            },
        )
        return

    band = competence.band
    status = {
        "strong": "MASTERED",
        "review": "IN_PROGRESS",
        "focus": "IN_PROGRESS",
    }.get(band or "focus", "IN_PROGRESS")

    await repo.update_topic_mastery(topic_id, competence.retention, status)


async def _compute_topic_breakdown(quiz_id: str, answers: list[Any]) -> list[dict]:
    """Compute per-topic score breakdown."""
    from sqlalchemy import select as sa_select

    from src.domains.personal_learning.db_models import (
        PrepQuestion,
        PrepTopic,
        QuizSessionQuestion,
    )
    from src.shared.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        # Reached through the session link, since a banked question is no longer
        # owned by the session that asked it.
        stmt = (
            sa_select(PrepQuestion)
            .join(
                QuizSessionQuestion,
                QuizSessionQuestion.prep_question_id == PrepQuestion.id,
            )
            .where(QuizSessionQuestion.quiz_session_id == quiz_id)
        )
        result = await session.execute(stmt)
        questions = list(result.scalars().all())

    # Group by topic
    topic_scores: dict[str, dict] = {}
    answer_map = {a.question_id: a for a in answers}

    for q in questions:
        topic_id = q.prep_topic_id or "general"
        if topic_id not in topic_scores:
            topic_scores[topic_id] = {"total": 0, "correct": 0, "title": "General"}
        topic_scores[topic_id]["total"] += 1
        answer = answer_map.get(q.id)
        if answer and answer.is_correct:
            topic_scores[topic_id]["correct"] += 1

    # Resolve topic titles
    if topic_scores:
        topic_ids = [k for k in topic_scores if k != "general"]
        if topic_ids:
            async with factory() as session:
                stmt = sa_select(PrepTopic).where(PrepTopic.id.in_(topic_ids))
                result = await session.execute(stmt)
                topics = list(result.scalars().all())
                for t in topics:
                    if t.id in topic_scores:
                        topic_scores[t.id]["title"] = t.title

    breakdown = []
    for tid, data in topic_scores.items():
        total = data["total"]
        correct = data["correct"]
        score = (correct / total * 100) if total > 0 else 0
        breakdown.append(
            {
                "topicId": tid,
                "title": data["title"],
                "total": total,
                "correct": correct,
                "score": round(score, 1),
            }
        )

    return breakdown


def _suggest_next_step(weak_areas: list[str]) -> str | None:
    """Suggest what to do next based on quiz results."""
    if not weak_areas:
        return "Great job! All topics are well covered. Try a Full Practice quiz next time."
    if len(weak_areas) == 1:
        return (
            f"Focus on reviewing: {weak_areas[0]}. Try a Topic Focus quiz to strengthen this area."
        )
    return f"Review these topics: {', '.join(weak_areas[:3])}. Try a Weak Areas quiz to improve."


# Question types where the learner picks from a fixed list the server supplied.
# The answer is closed, so an exact resolution to the correct option is both
# possible and required — see `_check_answer_correctness`.
CHOICE_QUESTION_TYPES = ("MULTIPLE_CHOICE", "TRUE_FALSE")


def _check_answer_correctness(
    user_answer: str,
    correct_answer: str,
    options: list[str] | dict | None,
    question_type: str | None = None,
) -> bool:
    """Check if a user's answer is correct using multiple matching strategies.

    Handles these cases:
    1. Direct text match (case-insensitive, stripped)
    2. Option index match: user sends "A"/"B"/"C"/"D" or "0"/"1"/"2"/"3",
       and correct_answer is the full text of an option (or vice versa)
    3. Prefix match: user sends "A. <text>" or "A) <text>"
    4. **Free-text only:** substring match, for a short answer where the learner
       adds extra context around the right answer.

    **The substring strategy must never apply to a choice question.** It used to
    apply to every type, which silently marked wrong answers correct: options
    routinely extend one another, so choosing "It increases then decreases" when
    the answer is "It increases" passed, because one contains the other. When the
    learner picked from a list the server supplied, there is no context to be
    generous about — the answer either resolves to the correct option or it does
    not, and being generous there does not help a learner, it lies to them about
    what they know.

    Returns True if the answer is considered correct by an applicable strategy.
    """
    # A closed answer set means exact resolution. Anything typed is open, so
    # `None` is treated as free text rather than assumed to be a choice.
    is_choice = (question_type or "").upper() in CHOICE_QUESTION_TYPES
    # Normalize
    user_norm = user_answer.strip().lower()
    correct_norm = correct_answer.strip().lower()

    # Strategy 1: Direct match
    if user_norm == correct_norm:
        return True

    # Strategy 2: Option index matching (A/B/C/D or 0/1/2/3)
    if options and isinstance(options, list) and len(options) > 0:
        # Normalize options
        options_norm = [str(o).strip().lower() for o in options]

        # Find the index of the correct answer in options
        correct_index = None
        for i, opt in enumerate(options_norm):
            if opt == correct_norm:
                correct_index = i
                break

        # Map letter/number to index
        index_map = {"a": 0, "b": 1, "c": 2, "d": 3, "e": 4, "f": 5}

        # Case: user sent an index (letter or number), correct_answer is option text
        user_index = index_map.get(user_norm)
        if user_index is None and user_norm.isdigit():
            user_index = int(user_norm)

        if user_index is not None and correct_index is not None:
            return user_index == correct_index

        # Case: user sent the full option text, correct_answer is a letter/number
        correct_as_index = index_map.get(correct_norm)
        if correct_as_index is None and correct_norm.isdigit():
            correct_as_index = int(correct_norm)

        if correct_as_index is not None and 0 <= correct_as_index < len(options_norm):
            # correct_answer is "A" or "0" — compare user's answer to the option text
            if user_norm == options_norm[correct_as_index]:
                return True

        # Case: user sent "A. <text>" or "A) <text>" — extract the text part
        if len(user_norm) >= 2 and user_norm[0] in index_map and user_norm[1] in ".):- ":
            user_text = user_norm[2:].strip().lstrip(".):- ").strip()
            if user_text == correct_norm:
                return True
            # Also check if the extracted text matches the option at that index
            letter_index = index_map[user_norm[0]]
            if correct_index is not None and letter_index == correct_index:
                return True

        # Case: user sent full option text, check if it matches the correct option
        if correct_index is not None and user_norm == options_norm[correct_index]:
            return True

        # Case: user's answer matches any option that matches the correct answer
        if user_norm in options_norm:
            user_option_index = options_norm.index(user_norm)
            if correct_index is not None and user_option_index == correct_index:
                return True

    # Strategy 3: one contains the other, for a typed answer that wraps the right
    # answer in extra words. **Free text only** — see the docstring.
    if not is_choice and len(correct_norm) > 3 and len(user_norm) > 3:
        if correct_norm in user_norm or user_norm in correct_norm:
            return True

    return False
