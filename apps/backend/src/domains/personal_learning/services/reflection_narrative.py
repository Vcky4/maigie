"""Composing a reflection's narrative: measured figures in, prose out.

Two rules govern this module, and between them they are the reason the Reflect programme exists.

**The model narrates. It never supplies a number** (Decision A). Every numeric field on the narrative
is filled here from `ReflectionMetrics` or the daily snapshot *before* the model is called, and the
model is asked only for wording. The defect this replaced did the opposite: it asked a model for
`topics_studied`, `sessions_completed` and `retention_score` while showing it nothing but a behaviour
profile, and stored the answers as measurements.

**The service chooses where an action points, never the model** (Decision O). A model free to emit an
`entityId` will eventually cite an entity the learner does not own — an authorization bug wearing a
recommendation's clothes. It writes `title`, `detail` and `label`; the target comes from entities this
service has already read under the learner's own id.

The Free/Plus split runs *inside* the narrative rather than gating the whole object (Decision T2).
Gating it wholesale would leave a Free learner opening the detail page to an empty hero and an empty
footer — the two largest text blocks on the screen. Free gets a shorter page, not a page with holes.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from .. import models

logger = logging.getLogger(__name__)

#: How many recommendations each tier receives (Decision T).
_FREE_RECOMMENDATIONS = 1
_PLUS_RECOMMENDATIONS = 3

#: Below this recall, retrieval practice is the thing worth suggesting.
_WEAK_RECALL_PERCENT = 70.0
#: Below this consistency, the useful suggestion is about showing up rather than about a subject.
_LOW_CONSISTENCY = 50.0


# ---------------------------------------------------------------------------
# The measured skeleton
# ---------------------------------------------------------------------------


def build_rhythm(snapshots: list[Any]) -> list[models.ReflectionRhythmDay]:
    """The week strip. Entirely measured, straight off the daily snapshot.

    `day` is an ISO date rather than a weekday label: the label is a formatting choice the client
    already makes elsewhere, and a monthly reflection would repeat "Mon" four times otherwise.

    Days without a snapshot are **absent**. The learner was not observed, and a zero-minute bar
    asserts they showed up and did nothing.
    """
    return [
        models.ReflectionRhythmDay(
            day=snapshot.snapshot_date.isoformat(),
            minutes=snapshot.focused_minutes,
            active=bool(snapshot.active_day),
        )
        for snapshot in snapshots
    ]


def build_signals(metrics: models.ReflectionMetrics) -> list[models.ReflectionSignal]:
    """The measured half of each signal card. Prose is added later, by the model, if at all.

    Only metrics that were actually measured become signals. A null metric produces no card rather
    than a card reading zero, which is why this returns a variable-length list.
    """
    candidates: tuple[tuple[str, str, float | None, str | None], ...] = (
        ("focus", "Focused time", metrics.focused_minutes, "min"),
        ("consistency", "Consistency", metrics.consistency_score, "/100"),
        ("recall", "Recall", metrics.recall_percent, "%"),
        ("accuracy", "Quiz accuracy", metrics.accuracy_percent, "%"),
        ("mastery", "Course completion gained", metrics.mastery_gained_percent, "pts"),
    )
    return [
        models.ReflectionSignal(id=signal_id, title=title, value=float(value), unit=unit)
        for signal_id, title, value, unit in candidates
        if value is not None
    ]


def build_subjects(subjects: list[Any]) -> list[models.ReflectionSubjectInsight]:
    """Subject rows carrying real mastery and real change. `insight` is left for the model.

    Numbers are metrics, so they are present on both tiers (Decision T2). Only the sentence is paid
    for.
    """
    return [
        models.ReflectionSubjectInsight(
            id=subject.course_id,
            title=subject.title,
            category=subject.category,
            mastery=subject.mastery_percent,
            change=subject.change,
        )
        for subject in subjects
    ]


# ---------------------------------------------------------------------------
# Recommendations — the service picks the target
# ---------------------------------------------------------------------------


def choose_actions(
    *,
    metrics: models.ReflectionMetrics,
    subjects: list[Any],
    limit: int,
) -> list[tuple[str, models.ReflectionActionTarget, str]]:
    """Pick what to suggest, and where each suggestion points.

    Returns `(id, target, grounds)` triples. `grounds` is a short factual phrase handed to the model
    so its wording can rest on the same evidence the target was chosen from — it is *input* to the
    prose, never published as prose itself.

    Every `entityId` here comes from a row already read under this learner's own id, which is the
    whole point of the service choosing rather than the model.
    """
    chosen: list[tuple[str, models.ReflectionActionTarget, str]] = []

    # Weakest subject with something left to do. `course` rather than a lesson, because the course
    # page is where a learner picks up where they left off.
    weakest = sorted(
        (s for s in subjects if s.topics_total > 0 and s.mastery_percent < 100),
        key=lambda s: s.mastery_percent,
    )
    if weakest:
        subject = weakest[0]
        chosen.append(
            (
                "weakest-subject",
                models.ReflectionActionTarget(
                    kind=models.ReflectionActionKind.COURSE, entity_id=subject.course_id
                ),
                f"{subject.title} is at {subject.mastery_percent:g}% "
                f"({subject.topics_completed} of {subject.topics_total} topics)",
            )
        )

    # Retrieval practice, when recall was measured and was weak. No `entityId`: the review queue is
    # assembled across decks by the scheduler, so naming one deck would send the learner somewhere
    # narrower than the thing being suggested.
    if metrics.recall_percent is not None and metrics.recall_percent < _WEAK_RECALL_PERCENT:
        chosen.append(
            (
                "recall",
                models.ReflectionActionTarget(kind=models.ReflectionActionKind.FLASHCARD_REVIEW),
                f"recall was {metrics.recall_percent:g}% this period",
            )
        )

    # Showing up at all, when consistency was measured and low. Ordered after the subject action
    # because a learner reading a reflection has already shown up once.
    if metrics.consistency_score is not None and metrics.consistency_score < _LOW_CONSISTENCY:
        chosen.append(
            (
                "consistency",
                models.ReflectionActionTarget(kind=models.ReflectionActionKind.SCHEDULE),
                f"consistency was {metrics.consistency_score:g} out of 100",
            )
        )

    return chosen[:limit]


# ---------------------------------------------------------------------------
# The prose
# ---------------------------------------------------------------------------


def build_prompt(
    *,
    type_: models.ReflectionType,
    period_start: datetime,
    period_end: datetime,
    facts: str,
    signals: list[models.ReflectionSignal],
    subjects: list[models.ReflectionSubjectInsight],
    actions: list[tuple[str, models.ReflectionActionTarget, str]],
) -> str:
    """Ask for wording, by id, for figures already computed.

    The model is given each signal and subject **with its number already attached** and asked for a
    sentence about it. It is never asked what the number is, and the instruction says so in the
    strongest terms the format allows — because the defect this replaced was exactly a prompt that
    asked for counts while supplying none.

    Keyed by id so the reply can be matched back to the right row. A positional list would silently
    attach the wrong insight to the wrong subject the first time the model dropped an item.
    """
    # `render_figure`, not `:g`. `%g` keeps six significant digits, which turned a consistency score
    # of 400/7 into `57.1429` — and the model, permitted to restate it verbatim, printed exactly that.
    signal_lines = "\n".join(
        f"- id \"{s.id}\": {s.title} = {render_figure(s.value)}{s.unit or ''}" for s in signals
    )
    subject_lines = "\n".join(
        f'- id "{s.id}": {s.title}, mastery {render_figure(s.mastery)}%'
        + (
            f", change {'+' if s.change >= 0 else ''}{render_figure(s.change)} points"
            if s.change is not None
            else ", change not measured"
        )
        for s in subjects
        if s.mastery is not None
    )
    action_lines = "\n".join(f'- id "{id_}": grounds — {grounds}' for id_, _, grounds in actions)

    return (
        f"Write a {type_.value} learning reflection for "
        f"{period_start.strftime('%Y-%m-%d')} to {period_end.strftime('%Y-%m-%d')}.\n\n"
        "MEASURED FACTS. These are the only figures that exist. You may restate any of them exactly "
        "as given. You must not compute a new figure, estimate, round differently, compare against a "
        "period you were not given, or mention any measurement absent from this brief:\n"
        f"{facts or '(nothing was measured this period)'}\n\n"
        f"SIGNALS to explain:\n{signal_lines or '(none)'}\n\n"
        f"SUBJECTS to comment on:\n{subject_lines or '(none)'}\n\n"
        f"ACTIONS to word:\n{action_lines or '(none)'}\n\n"
        "Return a JSON object with exactly these keys:\n"
        '- "opening": array of exactly 2 short paragraphs about the period\n'
        '- "theme": 1-4 words naming the period\'s character\n'
        '- "changeLabel": a short phrase for how it compared with before\n'
        '- "signals": object mapping each signal id to {"description", "evidence"}\n'
        '- "subjects": object mapping each subject id to an insight sentence\n'
        '- "patterns": {"keep": {"title","body"}, "watch": {"title","body"}} — omit either if '
        "there is not enough evidence for it\n"
        '- "closing": one encouraging sentence\n'
        '- "actions": object mapping each action id to {"title","detail","label"} where "label" is '
        "short call-to-action text\n\n"
        "Do not include any id that was not listed above. Return ONLY the JSON object."
    )


def render_figure(value: Any) -> str:
    """A measured figure as a human would write it.

    Ratios arrive at full precision — `consistencyScore` is a division, so it reads
    `57.14285714285714`. Both prompts tell the model it may restate a figure *exactly as given* and
    forbid rounding differently, which makes the precision we supply an instruction. A live deep
    narrative duly wrote "Your consistency score was 57.1429/100" into prose a learner reads.

    Rounding here keeps that instruction honest: the figure the model may repeat is now the figure the
    page would publish. One decimal, and no trailing `.0`, so a whole number of days does not read
    `4.0 days`.

    Shared by `reflection_service._render_facts` and `build_prompt` below, because a rule applied to
    one prompt and not the other is the same defect waiting on the other code path.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return str(value)
    if isinstance(value, int):
        return str(value)
    rounded = round(value, 1)
    return str(int(rounded)) if rounded == int(rounded) else str(rounded)


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


# Sentence terminators, plus the closers a quoted sentence can legitimately end on.
_SENTENCE_END = (".", "!", "?", '"', "'", "”", "’", "…")


def _finished_sentence(value: Any) -> str | None:
    """Prose the model actually finished, or `None`.

    `generate_content_json` repairs a truncated reply by closing the JSON, which is the right call —
    it rescues every field that did arrive whole. What it cannot do is tell that the *last* string it
    rescued stops mid-word. A deep narrative was observed publishing "…you will start turning" as its
    closing quote, which is the largest piece of text on the reflection page.

    So a passage that does not end on a terminator is treated as absent. That is the same judgement
    `_pattern` already makes about a title with no body: a fragment is worse than a gap, because a gap
    is visibly nothing while a fragment looks like something the model meant to say.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text if text.endswith(_SENTENCE_END) else None


def assemble(
    *,
    deep: bool,
    summary: str,
    written: dict[str, Any],
    signals: list[models.ReflectionSignal],
    subjects: list[models.ReflectionSubjectInsight],
    rhythm: list[models.ReflectionRhythmDay],
    highlights: list[str],
) -> models.ReflectionNarrative:
    """Fold the model's wording into the measured skeleton, honouring the tier split.

    On **Free** the paid prose is simply not attached: `signals`, `patterns` and `closing` are
    omitted, per-subject `insight` is dropped, and `opening` is the one-paragraph `summary`. What Free
    keeps is everything derived from measurement — subject numbers, the rhythm strip and the
    highlights — so the page is shorter rather than holed (Decision T2).

    Anything the model failed to write is left null. A missing insight is a subject row without a
    sentence, which reads as a subject row without a sentence; substituting filler would be the model
    appearing to say something about a subject it said nothing about.
    """
    if not deep:
        return models.ReflectionNarrative(
            opening=[summary] if summary else [],
            subjects=[s.model_copy(update={"insight": None}) for s in subjects],
            rhythm=rhythm,
            highlights=highlights,
        )

    signal_prose = _as_dict(written.get("signals"))
    subject_prose = _as_dict(written.get("subjects"))
    patterns_prose = _as_dict(written.get("patterns"))

    opening = [str(p).strip() for p in (written.get("opening") or []) if str(p).strip()]

    def _pattern(key: str) -> models.ReflectionPattern | None:
        entry = _as_dict(patterns_prose.get(key))
        title = str(entry.get("title") or "").strip()
        # A body cut off by truncation counts as no body, so the pair is dropped whole rather than
        # rendering a card whose prose stops mid-word.
        body = _finished_sentence(entry.get("body"))
        # Both or neither. A titled pattern with no body renders as a heading over blank space.
        if not title or not body:
            return None
        return models.ReflectionPattern(title=title, body=body)

    return models.ReflectionNarrative(
        # Falls back to the one-paragraph summary rather than to nothing, so a Plus learner whose
        # generation half-failed still gets the hero the page is built around.
        opening=opening or ([summary] if summary else []),
        theme=((str(written.get("theme")).strip()[:60] or None) if written.get("theme") else None),
        change_label=(
            (str(written.get("changeLabel")).strip()[:80] or None)
            if written.get("changeLabel")
            else None
        ),
        signals=[
            signal.model_copy(
                update={
                    "description": (
                        str(_as_dict(signal_prose.get(signal.id)).get("description") or "").strip()
                        or None
                    ),
                    "evidence": (
                        str(_as_dict(signal_prose.get(signal.id)).get("evidence") or "").strip()
                        or None
                    ),
                }
            )
            for signal in signals
        ],
        subjects=[
            subject.model_copy(
                update={"insight": (str(subject_prose.get(subject.id) or "").strip() or None)}
            )
            for subject in subjects
        ],
        rhythm=rhythm,
        patterns=models.ReflectionPatterns(keep=_pattern("keep"), watch=_pattern("watch")),
        highlights=highlights,
        closing=_finished_sentence(written.get("closing")),
    )


#: Deterministic wording per action id, for when no model wrote any.
#:
#: **This is the free tier's only action copy, not merely a degraded path.** Free composes its whole
#: reflection without a model (Decision M rule 2), so whatever is here is what a free learner reads —
#: which is why "Keep going" for every action is no longer good enough. It was an acceptable
#: last-resort string while it only appeared when a model call had failed; as the free tier's default
#: it would render the same three words over three quite different suggestions.
#:
#: Keyed by the ids `choose_actions` produces, so the two cannot drift apart silently: a new action
#: without a row here falls to the generic pair and a test names the gap.
_ACTION_COPY: dict[str, tuple[str, str]] = {
    "weakest-subject": ("Pick up where you left off", "Open course"),
    "recall": ("Run a review session", "Review cards"),
    "consistency": ("Book your next session", "Open schedule"),
}
_GENERIC_ACTION_COPY = ("Keep going", "Open")


def assemble_actions(
    *,
    chosen: list[tuple[str, models.ReflectionActionTarget, str]],
    written: dict[str, Any],
) -> list[models.ReflectionAction]:
    """Attach wording to the targets the service chose — the model's, or the deterministic copy.

    An action whose prose is missing still ships, with `grounds` as its detail. The target is the part
    that has to be right; the sentence is the part that can be plain.

    `grounds` is published as the detail here and nowhere else. `choose_actions` describes it as input
    to the prose "never published as prose itself", which holds for the *model* path — it is evidence
    handed over to be reworded. On the deterministic path there is nothing to reword, and a measured
    phrase like "Physics is at 40% (4 of 10 topics)" is a better detail line than any template could
    be, because it is the actual reason the action was chosen.
    """
    action_prose = _as_dict(written.get("actions"))
    actions: list[models.ReflectionAction] = []

    for id_, target, grounds in chosen:
        entry = _as_dict(action_prose.get(id_))
        default_title, default_label = _ACTION_COPY.get(id_, _GENERIC_ACTION_COPY)
        actions.append(
            models.ReflectionAction(
                id=id_,
                title=str(entry.get("title") or default_title).strip()[:120],
                detail=str(entry.get("detail") or grounds).strip()[:400],
                label=str(entry.get("label") or default_label).strip()[:40],
                target=target,
            )
        )
    return actions


def recommendation_limit(*, deep: bool) -> int:
    return _PLUS_RECOMMENDATIONS if deep else _FREE_RECOMMENDATIONS
