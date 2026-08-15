"""The rhythm the create wizard collects, and what the scheduler does with it.

Steps 1 and 2 of the wizard ask for a pace, a session length, preferred days and a path
shape. Until migration 023 all four were collected and thrown away: items landed on
consecutive calendar days sized from the learner's observed behaviour, and the phases
previewed on step 4 came from the client's template list while the plan was built with
whatever the model returned.

These are unit tests on purpose. Every function here is pure — dates in, dates out — and
the thing worth asserting is that an excluded weekday is not a date the scheduler can
produce, which no amount of database gets you closer to. The database-backed
round trip (create a plan with a rhythm, read it back) lives in ``test_study_plan_api.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.domains.personal_learning import plan_shapes
from src.domains.personal_learning.services import study_plan_service as svc

# A Monday, so weekday arithmetic in the assertions is readable.
MONDAY = datetime(2026, 8, 17, 9, 0, tzinfo=UTC)

# Mon, Wed, Fri, Sat — the wizard's own default selection.
MON_WED_FRI_SAT = [1, 3, 5, 6]


def _topics(count: int, minutes: int = 30) -> list[dict]:
    return [{"title": f"Topic {i}", "estimatedMinutes": minutes} for i in range(count)]


# ---------------------------------------------------------------------------
# Which days are available
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "given,expected",
    [
        (None, (1, 2, 3, 4, 5, 6, 7)),
        ([], (1, 2, 3, 4, 5, 6, 7)),
        ([1, 3], (1, 3)),
        # Out of range values are dropped, and dropping all of them is the same as never
        # having been asked.
        ([0, 8, 99], (1, 2, 3, 4, 5, 6, 7)),
        # Deduplicated and ordered, so the caller cannot depend on request order.
        ([5, 1, 5, 3], (1, 3, 5)),
    ],
)
def test_normalise_preferred_days(given, expected):
    assert svc._normalise_preferred_days(given) == expected


def test_available_dates_only_returns_chosen_weekdays():
    dates = svc._available_dates(MONDAY, 14, MON_WED_FRI_SAT)

    assert dates, "a two-week window contains several of these weekdays"
    assert {d.isoweekday() for d in dates} == {1, 3, 5, 6}
    assert dates == sorted(dates)


def test_available_dates_falls_back_when_no_chosen_day_fits_the_window():
    """A Sunday-only learner with a four-day deadline still gets a schedulable plan.

    The deadline is the harder constraint: honouring availability exactly here would mean
    a plan with nowhere to put its items, which is worse than a plan on the wrong weekday.
    """
    dates = svc._available_dates(MONDAY, 4, [7])

    assert len(dates) == 4
    assert [d.isoweekday() for d in dates] == [1, 2, 3, 4]


def test_available_dates_with_no_preference_is_every_day_in_the_window():
    assert len(svc._available_dates(MONDAY, 10, None)) == 10


# ---------------------------------------------------------------------------
# Distribution
# ---------------------------------------------------------------------------


def test_distribute_items_never_schedules_on_an_excluded_weekday():
    """The defect this closes: "Mon/Wed/Fri/Sat" used to produce work on the Tuesday."""
    items = svc._distribute_items(
        _topics(12), days_available=21, start=MONDAY, max_daily_minutes=30,
        preferred_days=MON_WED_FRI_SAT,
    )

    assert len(items) == 12
    assert {i["scheduledDate"].isoweekday() for i in items} <= {1, 3, 5, 6}


def test_distribute_items_treats_the_session_length_as_the_day_budget():
    """Two 30-minute topics do not both fit a 35-minute session."""
    items = svc._distribute_items(
        _topics(4, minutes=30), days_available=30, start=MONDAY, max_daily_minutes=35
    )

    dates = [i["scheduledDate"] for i in items]
    assert len(set(dates)) == 4, "each topic needs its own day at this budget"


def test_distribute_items_packs_a_day_up_to_the_budget():
    items = svc._distribute_items(
        _topics(4, minutes=25), days_available=30, start=MONDAY, max_daily_minutes=50
    )

    dates = [i["scheduledDate"] for i in items]
    assert len(set(dates)) == 2, "two 25-minute topics fit a 50-minute day"


def test_distribute_items_without_a_preference_starts_today():
    """Unchanged behaviour: a fresh plan gives the learner something to do immediately."""
    items = svc._distribute_items(
        _topics(1), days_available=14, start=MONDAY, max_daily_minutes=60
    )

    assert items[0]["scheduledDate"] == MONDAY


def test_distribute_items_wraps_rather_than_scheduling_past_the_window():
    items = svc._distribute_items(
        _topics(10, minutes=60), days_available=3, start=MONDAY, max_daily_minutes=60
    )

    latest = max(i["scheduledDate"] for i in items)
    assert (latest - MONDAY).days < 3


# ---------------------------------------------------------------------------
# Review interleaving
# ---------------------------------------------------------------------------


def test_review_items_land_on_available_days_too():
    """A review three days after a Saturday study item would otherwise fall on Tuesday.

    Half-honouring the preference is more confusing than ignoring it: the learner sees
    every study item on a chosen day and a review that is not.
    """
    study = svc._distribute_items(
        _topics(9), days_available=28, start=MONDAY, max_daily_minutes=30,
        preferred_days=MON_WED_FRI_SAT,
    )
    reviews = svc._add_review_items(
        study, days_available=28, start=MONDAY, preferred_days=MON_WED_FRI_SAT
    )

    assert reviews, "a third of nine topics is three reviews"
    assert {r["scheduledDate"].isoweekday() for r in reviews} <= {1, 3, 5, 6}


def test_review_items_are_never_earlier_than_the_study_item():
    study = svc._distribute_items(
        _topics(9), days_available=28, start=MONDAY, max_daily_minutes=30,
        preferred_days=MON_WED_FRI_SAT,
    )
    reviews = svc._add_review_items(
        study, days_available=28, start=MONDAY, preferred_days=MON_WED_FRI_SAT
    )

    for review in reviews:
        source = next(i for i in study if review["title"] == f"Review: {i['title']}")
        assert review["scheduledDate"] > source["scheduledDate"]


def test_review_items_are_dropped_rather_than_scheduled_past_the_plan_end():
    study = svc._distribute_items(
        _topics(6), days_available=2, start=MONDAY, max_daily_minutes=30
    )
    reviews = svc._add_review_items(study, days_available=2, start=MONDAY)

    assert reviews == []


# ---------------------------------------------------------------------------
# Phases follow the chosen shape
# ---------------------------------------------------------------------------


SHAPE_PHASES = ["Map the system", "Learn the core patterns", "Solve realistic cases"]


def test_conform_phases_keeps_labels_the_model_got_right():
    topics = [
        {"title": "a", "phase": "map the system"},
        {"title": "b", "phase": "Learn the core  patterns"},
        {"title": "c", "phase": "Solve realistic cases"},
    ]

    result = svc._conform_phases(topics, SHAPE_PHASES)

    # Matched case- and space-insensitively, then rewritten to the catalogue's spelling so
    # the roadmap headings read exactly as the wizard showed them.
    assert [t["phase"] for t in result] == SHAPE_PHASES


def test_conform_phases_relabels_positionally_when_the_model_invents_its_own():
    topics = [{"title": str(i), "phase": "Fundamentals"} for i in range(6)]

    result = svc._conform_phases(topics, SHAPE_PHASES)

    assert [t["phase"] for t in result] == [
        "Map the system",
        "Map the system",
        "Learn the core patterns",
        "Learn the core patterns",
        "Solve realistic cases",
        "Solve realistic cases",
    ]


def test_conform_phases_is_all_or_nothing_per_plan():
    """One wrong label relabels every topic, rather than mixing the two schemes.

    Mixing would produce more phases than the shape has, and a roadmap with four headings
    where the preview showed three is worse than one that is uniformly positional.
    """
    topics = [
        {"title": "a", "phase": "Map the system"},
        {"title": "b", "phase": "Something else entirely"},
    ]

    result = svc._conform_phases(topics, SHAPE_PHASES)

    assert set(t["phase"] for t in result) <= set(SHAPE_PHASES)
    assert "Something else entirely" not in [t["phase"] for t in result]


def test_conform_phases_never_produces_more_phases_than_the_shape_has():
    for count in range(1, 40):
        topics = [{"title": str(i), "phase": None} for i in range(count)]
        result = svc._conform_phases(topics, SHAPE_PHASES)
        labels = [t["phase"] for t in result]
        assert set(labels) <= set(SHAPE_PHASES), count
        # And never runs off the end, which an `index * count // len` without the clamp
        # would do for the final topic.
        assert labels[-1] in SHAPE_PHASES, count


def test_conform_phases_leaves_an_unshaped_plan_alone():
    topics = [{"title": "a", "phase": "Whatever the model said"}]

    assert svc._conform_phases(topics, [])[0]["phase"] == "Whatever the model said"


# ---------------------------------------------------------------------------
# The shape catalogue
# ---------------------------------------------------------------------------


def test_shape_ids_match_the_catalogue():
    assert plan_shapes.SHAPE_IDS == {s["id"] for s in plan_shapes.PLAN_SHAPES}
    assert len(plan_shapes.SHAPE_IDS) == len(plan_shapes.PLAN_SHAPES), "ids must be unique"


def test_every_shape_is_renderable_by_the_wizard():
    """The wizard renders each of these; a missing key is a blank in the UI."""
    for shape in plan_shapes.PLAN_SHAPES:
        assert shape.keys() >= {
            "id",
            "title",
            "category",
            "description",
            "defaultTitle",
            "defaultOutcome",
            "phases",
        }, shape.get("id")
        assert shape["phases"], shape["id"]
        for phase in shape["phases"]:
            assert phase.keys() >= {"id", "title", "description", "duration", "outcomes"}
            assert phase["outcomes"]


def test_phase_titles_are_unique_within_a_shape():
    """Two phases sharing a title would collapse into one group on the roadmap."""
    for shape in plan_shapes.PLAN_SHAPES:
        titles = [p["title"] for p in shape["phases"]]
        assert len(titles) == len(set(titles)), shape["id"]


def test_find_shape_and_phase_titles_tolerate_an_unknown_id():
    """A retired shape must not break a plan that still references it."""
    assert plan_shapes.find_shape("no-such-shape") is None
    assert plan_shapes.find_shape(None) is None
    assert plan_shapes.phase_titles("no-such-shape") == []
    assert plan_shapes.phase_titles(None) == []


def test_phase_titles_returns_the_catalogue_order():
    shape = plan_shapes.find_shape("skill-mastery")
    assert plan_shapes.phase_titles("skill-mastery") == [p["title"] for p in shape["phases"]]


# ---------------------------------------------------------------------------
# The rhythm survives the repository's field map
# ---------------------------------------------------------------------------


def test_repository_field_map_carries_every_rhythm_field():
    """`_map_study_plan` drops keys it does not recognise, silently.

    That is how partial updates work, so it cannot raise — which means a field added to the
    model and forgotten here would be discarded on write with every test still passing.
    This is the assertion that would have caught it.
    """
    from src.domains.personal_learning.repository import personal_learning_repo as repo

    mapped = repo._map_study_plan(
        {
            "sessionsPerWeek": 5,
            "sessionMinutes": 35,
            "preferredDays": [1, 3, 5, 6],
            "shape": "skill-mastery",
        }
    )

    assert mapped == {
        "sessions_per_week": 5,
        "session_minutes": 35,
        "preferred_days": [1, 3, 5, 6],
        "shape": "skill-mastery",
    }


# ---------------------------------------------------------------------------
# The daily budget, and refusing a shape that does not exist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "session_minutes,observed,expected",
    [
        # A number the learner typed is taken at face value, in both directions. Clamping
        # it by observed behaviour would discard the answer while appearing to honour it.
        (20, 90, 20.0),
        (50, 20, 50.0),
        # Nothing stated: the previous behaviour, one and a half average sessions.
        (None, 40, 60.0),
        # ...capped at two hours.
        (None, 200, 120.0),
        # Zero is not a session length. Treated as unstated rather than as a day that
        # holds nothing, which would put every item on the same date.
        (0, 40, 60.0),
    ],
)
async def test_daily_minute_budget(monkeypatch, session_minutes, observed, expected):
    class _Profile:
        avg_session_minutes = observed

    async def _profile(_user_id):
        return _Profile()

    monkeypatch.setattr(svc.repo, "get_profile_by_user", _profile)

    assert await svc._daily_minute_budget("user-1", session_minutes) == expected


async def test_daily_minute_budget_defaults_when_there_is_no_profile(monkeypatch):
    async def _none(_user_id):
        return None

    monkeypatch.setattr(svc.repo, "get_profile_by_user", _none)

    assert await svc._daily_minute_budget("user-1", None) == 90.0


async def test_generate_plan_refuses_an_unknown_shape():
    """Refused rather than stored and ignored.

    The shape is not a label: its phases are the structure the generator is told to follow,
    so accepting an id with no catalogue entry would build an ungrouped plan straight after
    the wizard showed the learner a four-phase roadmap. Checked before any tier lookup or
    model call, so a bad request costs nothing.
    """
    from src.shared.exceptions import ValidationError

    with pytest.raises(ValidationError) as excinfo:
        await svc.generate_plan(
            user_id="user-1",
            data={"title": "Anything", "deadline": None, "shape": "not-a-shape"},
        )

    assert "shape" in str(excinfo.value).lower()


async def test_generate_plan_accepts_every_catalogued_shape():
    """The complement: a real id must get past the same check.

    Without this, a check with the comparison inverted would still pass the test above.
    Generation is stopped at the tier lookup, the first thing after validation, so the
    assertion is about the check and not about everything downstream of it.
    """
    from src.shared.exceptions import ValidationError

    class _Stop(Exception):
        pass

    async def _stop(_user_id):
        raise _Stop()

    for shape_id in sorted(plan_shapes.SHAPE_IDS):
        with pytest.raises(_Stop):
            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(
                    "src.domains.personal_learning.services.feature_tier_service"
                    ".get_quality_tier",
                    _stop,
                )
                await svc.generate_plan(
                    user_id="user-1",
                    data={"title": "Anything", "deadline": None, "shape": shape_id},
                )

    # And the invalid case really is stopped by validation, not by the same patch.
    with pytest.raises(ValidationError):
        await svc.generate_plan(
            user_id="user-1", data={"title": "Anything", "deadline": None, "shape": "nope"}
        )


# ---------------------------------------------------------------------------
# Which phase a plan is in
# ---------------------------------------------------------------------------


def _phase(number: int, label: str, completed: int, total: int) -> dict:
    return {
        "label": label,
        "number": number,
        "start": MONDAY,
        "end": MONDAY,
        "completed_items": completed,
        "total_items": total,
    }


class _Item:
    def __init__(self, phase=None):
        self.phase = phase


def test_current_phase_is_the_one_holding_the_next_pending_item():
    phases = [_phase(1, "Foundations", 2, 2), _phase(2, "Practice", 0, 3)]

    current = svc._current_phase(phases, _Item("Practice"))

    assert current["label"] == "Practice"


def test_current_phase_of_a_finished_plan_is_its_last_one():
    """Not its first. A completed plan reported as being at its beginning reads as a bug."""
    phases = [_phase(1, "Foundations", 2, 2), _phase(2, "Practice", 3, 3)]

    assert svc._current_phase(phases, None)["label"] == "Practice"


def test_current_phase_falls_back_to_the_earliest_unfinished_phase():
    """An item can be pending and carry no phase on a plan whose other items do.

    Position is not usable as a fallback here — the numbers are derived from the same rows —
    so "earliest phase with work left" is used, which is what current means anyway.
    """
    phases = [_phase(1, "Foundations", 2, 2), _phase(2, "Practice", 1, 3)]

    assert svc._current_phase(phases, _Item(None))["label"] == "Practice"


def test_current_phase_ignores_a_label_no_phase_claims():
    """A stale label must not return None and blank the card."""
    phases = [_phase(1, "Foundations", 0, 2)]

    assert svc._current_phase(phases, _Item("Retired label"))["label"] == "Foundations"


def test_current_phase_of_an_ungrouped_plan_is_none():
    assert svc._current_phase([], _Item("Anything")) is None
    assert svc._current_phase([], None) is None
