"""Prose for the growth surfaces, written about figures the service already measured.

Two passages live here: the drivers beneath the growth chart, and a subject's strength/focus pair with
its recommended next step. Both follow the rule `reflection_narrative` established — **the service
measures every number and chooses every target, and the model is asked only for wording** (Decisions
A and O). A prompt that asks for a count while supplying none is the defect that machinery exists to
close, and there is no reason to reopen it on a second surface.

Both are Plus (Decision Z) and both are stored by `narrative_cache` against a fingerprint of the
skeleton below, so a learner opening a subject twice does not pay for two generations.

The split of guards is deliberate and matches `reflection_narrative.assemble`: a **heading** is a
phrase, so it is trimmed to a length; a **paragraph** is a sentence, so one that does not end on a
terminator is treated as absent. A fragment under a chart reads as a finding.
"""

from __future__ import annotations

import logging
from typing import Any

from .. import models
from .reflection_narrative import _as_dict, _finished_sentence, render_figure

logger = logging.getLogger(__name__)

#: Points of movement at which a measure is called out as the dominant one.
_HIGH_IMPACT_POINTS = 10.0

#: First-attempt accuracy below which a subject's knowledge checks are worth naming as the focus.
_WEAK_CHECK_PERCENT = 70.0


def _impact(change: float | None) -> models.GrowthDriverImpact:
    """A movement's magnitude as a token.

    `steady` for an exactly flat measure, which is a real finding across thirty days and not the same
    as `growing`. `slipping` for any loss, at any size — a decline does not get graded, because the
    panel's job is to surface it rather than to reassure about how small it is.
    """
    if change is None or change == 0:
        return "steady"
    if change < 0:
        return "slipping"
    return "high" if change >= _HIGH_IMPACT_POINTS else "growing"


def build_drivers(trends: models.GrowthTrendsResponse) -> list[dict[str, Any]]:
    """The measured skeleton of the drivers panel, richest movement first.

    Reads only the response the chart itself renders, which is what keeps the two in agreement — the
    same rule `reflect_dashboard_service.get_dashboard` follows for its summary ring. No queries here.

    **A measure with no movement to report is omitted, not published as zero.** `GrowthDelta.change`
    is `None` when the range holds fewer than two days of that series, and `_delta` documents why: one
    observation is not a trend. A driver card for it would be a claim about a change nobody measured.

    Retrieval is the fourth candidate and is derived from the points rather than from a delta, because
    there is no `recall` series on the response — the cards and the recall percentages are per-day
    values on `points`, so first-against-last is computed here over the days that hold one.
    """
    candidates: list[dict[str, Any]] = []

    def offer(id_: str, change: float | None, last: Any, figures: dict, evidence: str) -> None:
        """Add a candidate, unless there is nothing for it to be about.

        Two rejections, and they are different:

        `change is None` — fewer than two days of the series were captured, so no movement was
        measured. `_delta` documents why one observation is not a trend.

        **`change == 0` and `last` is zero or absent — the series is flat at nothing.** This is the one
        the live data taught: a learner with no tracked study has an effort series of zeros, and without
        this guard the panel published a card reading "steady · 0 effort · 0 focused minutes". Nothing
        drove that, so there is no driver, and asking a model to explain it would produce a sentence
        about a behaviour that did not happen. A series flat at a *real* value is kept — holding
        consistency at 100 for thirty days is a genuine finding.
        """
        if change is None:
            return
        if change == 0 and not last:
            return
        candidates.append(
            {"id": id_, "change": change, "figures": figures, "evidence": evidence}
        )

    offer(
        "consistency",
        trends.consistency.change,
        trends.consistency.last,
        {
            "consistency score": trends.consistency.last,
            "active days": trends.active_days,
            "days captured": trends.captured_days,
        },
        _evidence(
            _count(trends.active_days, "active day", "active days"),
            _score("consistency", trends.consistency.last),
        ),
    )

    minutes = _total(p.focused_minutes for p in trends.points)
    offer(
        "effort",
        trends.effort.change,
        trends.effort.last,
        {"effort score": trends.effort.last, "focused minutes": minutes},
        _evidence(_score("effort", trends.effort.last), _count(minutes, "focused minute", "focused minutes")),
    )

    topics = _total(p.topics_completed for p in trends.points)
    offer(
        "mastery",
        trends.mastery.change,
        trends.mastery.last,
        {"mastery percent": trends.mastery.last, "topics completed": topics},
        _evidence(_percent(trends.mastery.last, "mastery"), _count(topics, "topic completed", "topics completed")),
    )

    recall = [p.recall_percent for p in trends.points if p.recall_percent is not None]
    cards = _total(p.cards_reviewed for p in trends.points)
    if len(recall) >= 2 and cards:
        offer(
            "retrieval",
            round(recall[-1] - recall[0], 1),
            recall[-1],
            {"recall percent": recall[-1], "cards reviewed": cards},
            _evidence(_count(cards, "card reviewed", "cards reviewed"), _percent(recall[-1], "recall")),
        )

    # Largest absolute movement first. Ordering by magnitude rather than by signed change so a sharp
    # decline leads the panel instead of sinking to the bottom of it, which is the one ordering a
    # learner losing consistency needs.
    candidates.sort(key=lambda c: abs(c["change"] or 0), reverse=True)
    for candidate in candidates:
        candidate["impact"] = _impact(candidate["change"])
    return candidates


def _total(values) -> float:
    """Sum of the values that exist, rounded to a figure a page would print.

    `0` and "never measured" are not distinguished here, unlike everywhere else, and the reason is
    narrow: this feeds an `evidence` string that only renders at all when its measure moved, so the
    series was captured by construction.
    """
    total = sum(v for v in values if v is not None)
    return round(total, 1) if isinstance(total, float) else total


def _evidence(*parts: str | None) -> str:
    """Measured figures joined the way the panel prints them, skipping the ones with no value.

    Three small renderers rather than one general one, because "19 active days", "consistency 86/100"
    and "74% mastery" are three natural phrasings and forcing them through a single template produced
    "100 consistency score" in a live run — grammatical enough to pass review and wrong enough to
    notice on the page.
    """
    return " · ".join(part for part in parts if part)


def _count(value: Any, singular: str, plural: str) -> str | None:
    """`19 active days`, pluralised. Published prose, so `1 active days` is not acceptable.

    **Both forms are spelled out rather than derived by appending `s`.** Two of the four nouns here end
    in a participle — `card reviewed`, `topic completed` — and a suffix rule renders those as
    `12 card revieweds`. The plural of a noun phrase is not a property of its last character.
    """
    if value is None:
        return None
    rendered = render_figure(value)
    return f"{rendered} {singular}" if rendered == "1" else f"{rendered} {plural}"


def _score(name: str, value: Any) -> str | None:
    """`consistency 86/100`. The denominator is what makes a bare 86 readable.

    `render_figure`, not `str`: a consistency score is a division and arrives as `57.14285714285714`.
    The reflection prompt learned that the expensive way, and this string is published directly rather
    than merely offered to a model, so the rounding matters more here, not less.
    """
    if value is None:
        return None
    return f"{name} {render_figure(value)}/100"


def _percent(value: Any, noun: str) -> str | None:
    """`74% mastery`."""
    if value is None:
        return None
    return f"{render_figure(value)}% {noun}"


def build_drivers_prompt(*, range_: str, skeleton: list[dict[str, Any]]) -> str:
    """Ask for a heading and a sentence per driver id, with every figure already attached."""
    lines = "\n".join(
        f'- id "{c["id"]}": moved {"+" if (c["change"] or 0) >= 0 else ""}'
        f"{render_figure(c['change'])} points across the range; "
        + ", ".join(f"{label} {render_figure(value)}" for label, value in c["figures"].items())
        for c in skeleton
    )
    return (
        f"A learner's growth over the last {range_}. Explain what drove each movement.\n\n"
        "MEASURED FACTS. These are the only figures that exist. You may restate any of them exactly "
        "as given. You must not compute a new figure, estimate, round differently, compare against a "
        "period you were not given, or mention any measurement absent from this brief:\n"
        f"{lines or '(nothing moved measurably)'}\n\n"
        "For each id write a heading of at most eight words naming what changed in the learner's "
        "behaviour, and one sentence of at most thirty words explaining how that behaviour produced "
        "the movement. Address the learner as \"you\".\n"
        # Both figures are already published beside the sentence — `evidence` and `change` are
        # rendered by the service — and a live run produced "resulting in a movement of +0 points
        # across the range", which is the card reciting its own badge instead of explaining anything.
        "Write about the behaviour, not the arithmetic: do not restate the point movement or the "
        "score in either the heading or the sentence, because both are already displayed beside "
        "your words.\n\n"
        "Return a JSON object with one key per id above, each mapping to "
        '{"title", "detail"}. Do not include any id that was not listed. Return ONLY the JSON object.'
    )


def assemble_drivers(
    *, skeleton: list[dict[str, Any]], written: dict[str, Any]
) -> list[models.GrowthDriver]:
    """Fold the wording into the measured skeleton.

    **A driver with no heading is dropped.** Unlike a reflection action — which ships with its grounds
    as a fallback because its target is the part that must be right — a driver card is nothing but its
    interpretation; the figures beside it are already on the chart above. An untitled card would be an
    impact badge over blank space.

    `detail` may be `null` on a card that has a heading: the heading is a phrase and survives
    truncation, the sentence does not.
    """
    drivers: list[models.GrowthDriver] = []
    for candidate in skeleton:
        entry = _as_dict(written.get(candidate["id"]))
        title = str(entry.get("title") or "").strip()[:80]
        if not title:
            continue
        drivers.append(
            models.GrowthDriver(
                id=candidate["id"],
                title=title,
                detail=_finished_sentence(entry.get("detail")),
                evidence=candidate["evidence"],
                impact=candidate["impact"],
                change=candidate["change"],
            )
        )
    return drivers


# ---------------------------------------------------------------------------
# Subject insight
# ---------------------------------------------------------------------------


#: The next-step ladder, as `(label, kind)` pairs. The label travels with the kind so a button's words
#: and its destination cannot be chosen separately and end up disagreeing.
_STEP_SCHEDULE = ("Plan a session", models.ReflectionActionKind.SCHEDULE)
_STEP_COURSE = ("Open the course", models.ReflectionActionKind.COURSE)
_STEP_REVIEW = ("Review to keep it", models.ReflectionActionKind.FLASHCARD_REVIEW)


def choose_next_step(detail: models.GrowthSubjectDetailResponse) -> tuple[str, str, models.ReflectionActionTarget]:
    """`(reason, label, target)` for the recommended next move on this subject.

    A ladder over what was measured, in the order a learner is blocked:

    1. **Nothing recorded this range** — the step is to get a session on the calendar, not to study a
       topic. Recommending topic work to someone who has not opened the subject skips the actual gap.
    2. **A topic needs attention** — named, and the target is the course, because `ReflectionActionKind`
       has no per-topic route and inventing one here would put a URL in the backend.
    3. **Topics not started** — continue the course.
    4. **Everything strong** — review, so it stays that way. Reached only when there is genuinely
       nothing to fix, which is the one case where a "keep going" recommendation is a finding.

    `reason` is the grounds, and is handed to the prompt rather than to the client: it tells the model
    what the step is *for* so the sentence it writes is about the right thing.
    """
    activity = detail.subject.activity
    concepts = detail.concepts
    course_id = detail.subject.course_id

    if activity is not None and activity.sessions == 0:
        return (
            "no study sessions were recorded on this subject across the range",
            _STEP_SCHEDULE[0],
            models.ReflectionActionTarget(kind=_STEP_SCHEDULE[1]),
        )

    weak = next((c for c in concepts if c.status == "needs_attention"), None)
    if weak is not None:
        return (
            f"the topic \"{weak.title}\" is the least secure of "
            f"{len(concepts)} in this subject",
            _STEP_COURSE[0],
            models.ReflectionActionTarget(kind=_STEP_COURSE[1], entity_id=course_id),
        )

    unstarted = [c for c in concepts if c.status == "not_started"]
    if unstarted:
        return (
            f"{len(unstarted)} of {len(concepts)} topics have not been started",
            _STEP_COURSE[0],
            models.ReflectionActionTarget(kind=_STEP_COURSE[1], entity_id=course_id),
        )

    return (
        "every measured topic in this subject is secure",
        _STEP_REVIEW[0],
        models.ReflectionActionTarget(kind=_STEP_REVIEW[1]),
    )


def build_subject_skeleton(detail: models.GrowthSubjectDetailResponse) -> dict[str, Any]:
    """Every figure the subject insight is written from, and nothing else.

    Built from the detail response the page already renders, so the sentence and the numbers beside it
    come from one read. Concept **band counts** rather than the whole list: the prose is about the
    shape of the subject, and a fifty-topic course would otherwise put fifty titles into a prompt to
    produce two paragraphs.

    Only the weakest topic is named, because that is the only one the focus paragraph can be about.
    """
    subject = detail.subject
    activity = detail.subject.activity
    bands = {"strong": 0, "growing": 0, "needs_attention": 0, "not_started": 0}
    for concept in detail.concepts:
        bands[concept.status] = bands.get(concept.status, 0) + 1

    weak = next((c for c in detail.concepts if c.status == "needs_attention"), None)
    skeleton: dict[str, Any] = {
        "title": subject.title,
        "masteryPercent": subject.mastery_percent,
        "change": subject.change,
        "topicsCompleted": subject.topics_completed,
        "topicsTotal": subject.topics_total,
        "bands": bands,
        "weakestTopic": weak.title if weak is not None else None,
        "evidenceCount": len(detail.evidence),
    }
    if activity is not None:
        skeleton["activity"] = {
            "sessions": activity.sessions,
            "focusedMinutes": activity.focused_minutes,
            "activeDays": activity.active_days,
            "knowledgeChecksAnswered": activity.knowledge_checks_answered,
            "knowledgeCheckAccuracyPercent": activity.knowledge_check_accuracy_percent,
        }
    return skeleton


def build_subject_prompt(*, skeleton: dict[str, Any], range_: str, reason: str) -> str:
    """Ask for two headings, two paragraphs and a next step, over figures already attached.

    The focus half is told what the recommendation will be, so the paragraph and the button beneath it
    argue for the same thing. Without that the model regularly proposed one thing while the service
    linked to another.
    """
    facts = [f"subject: {skeleton['title']}"]
    if skeleton.get("masteryPercent") is not None:
        facts.append(f"mastery: {render_figure(skeleton['masteryPercent'])}%")
    if skeleton.get("change") is not None:
        sign = "+" if skeleton["change"] >= 0 else ""
        facts.append(f"mastery change across the range: {sign}{render_figure(skeleton['change'])} points")
    else:
        facts.append("mastery change: not measured across this range")
    facts.append(
        f"topics completed: {skeleton['topicsCompleted']} of {skeleton['topicsTotal']}"
    )
    bands = skeleton["bands"]
    facts.append(
        "topic standing: "
        f"{bands['strong']} secure, {bands['growing']} in progress, "
        f"{bands['needs_attention']} needing attention, {bands['not_started']} not started"
    )
    if skeleton.get("weakestTopic"):
        facts.append(f"least secure topic: {skeleton['weakestTopic']}")
    activity = skeleton.get("activity") or {}
    for key, label in (
        ("sessions", "study sessions recorded on this subject"),
        ("focusedMinutes", "minutes of tracked study on this subject"),
        ("activeDays", "days with a session on this subject"),
        ("knowledgeChecksAnswered", "knowledge checks first answered"),
    ):
        if activity.get(key) is not None:
            facts.append(f"{label}: {render_figure(activity[key])}")
    if activity.get("knowledgeCheckAccuracyPercent") is not None:
        facts.append(
            "share of those first attempts correct: "
            f"{render_figure(activity['knowledgeCheckAccuracyPercent'])}%"
        )

    return (
        f"A learner's progress on one subject over the last {range_}.\n\n"
        "MEASURED FACTS. These are the only figures that exist. You may restate any of them exactly "
        "as given. You must not compute a new figure, estimate, round differently, compare against a "
        "period you were not given, or mention any measurement absent from this brief:\n"
        + "\n".join(f"- {fact}" for fact in facts)
        + "\n\nThe recommended next step has already been chosen, on these grounds: "
        f"{reason}. Write the focus paragraph and the step so that both argue for it.\n\n"
        "Address the learner as \"you\". Return a JSON object with exactly these keys:\n"
        '- "strength": a heading of at most seven words naming what is working\n'
        '- "strengthDetail": one sentence of at most thirty words supporting it\n'
        '- "focus": a heading of at most seven words naming where to look next\n'
        '- "focusDetail": one sentence of at most thirty words explaining the gap\n'
        '- "step": {"title", "detail"} — a heading of at most eight words for the recommended '
        "step, and one sentence of at most thirty words saying what to do\n\n"
        "Do not claim the learner mastered anything the topic standing does not support. "
        "Return ONLY the JSON object."
    )


def assemble_subject(
    *, written: dict[str, Any], label: str, target: models.ReflectionActionTarget, reason: str
) -> tuple[models.SubjectInsight | None, models.SubjectNextStep | None]:
    """`(insight, nextStep)` from the wording, either of which may be absent.

    **Both headings or no insight.** The page renders "What is working" and "Where to focus" as a
    matched pair of cards; one filled and one empty reads as a page that failed rather than as a
    subject with only good news.

    The step, in contrast, ships whenever it has a heading, and falls back to the service's own grounds
    for its sentence — the target is the part that must be right, and the grounds are a true statement
    about the subject even when they are plainer than the model's would have been. That is the same
    trade `assemble_actions` makes.
    """
    strength = str(written.get("strength") or "").strip()[:80]
    focus = str(written.get("focus") or "").strip()[:80]
    insight = (
        models.SubjectInsight(
            strength=strength,
            strength_detail=_finished_sentence(written.get("strengthDetail")),
            focus=focus,
            focus_detail=_finished_sentence(written.get("focusDetail")),
        )
        if strength and focus
        else None
    )

    step_prose = _as_dict(written.get("step"))
    step_title = str(step_prose.get("title") or "").strip()[:100]
    next_step = (
        models.SubjectNextStep(
            title=step_title,
            detail=_finished_sentence(step_prose.get("detail")) or reason,
            label=label,
            target=target,
        )
        if step_title
        else None
    )
    return insight, next_step
