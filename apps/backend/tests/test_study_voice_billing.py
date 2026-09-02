"""Voice billing arithmetic and session bookkeeping.

Voice is the only thing in the product billed by time rather than tokens, which means these are the only
figures that cannot be re-derived from a provider response after the fact. If the accrual and the settlement
disagree, the learner is either charged twice for the same minute or not charged for the last one, and neither
shows up anywhere except a support ticket.

The arithmetic is pure, so it is tested directly rather than through a socket. The billing *loop* that drives
it needs a fake provider to test and is called out in the design document as the highest-value test asset
still missing.
"""

from types import SimpleNamespace

import pytest

from src.domains.study_voice import billing, session_store
from src.domains.study_voice.bridge import BridgeState, study_tools_for


@pytest.fixture
def settings(monkeypatch):
    """Fixed billing settings, so these assertions do not move when pricing does."""
    fake = SimpleNamespace(
        GEMINI_LIVE_UNITS_PER_MINUTE=100.0,
        GEMINI_LIVE_MIN_SESSION_UNITS=500,
        GEMINI_LIVE_STANDBY_IDLE_SECONDS=2.5,
        GEMINI_LIVE_BILLING_TICK_SECONDS=2.0,
        GEMINI_LIVE_BILLING_MIN_CONSUME_CHUNK=50,
        GEMINI_LIVE_BILLING_FLUSH_INTERVAL_SECONDS=60.0,
    )
    monkeypatch.setattr(billing, "get_settings", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# The rate itself
# ---------------------------------------------------------------------------
#
# Everything below this section uses the fixed fixture above, deliberately: the arithmetic should not
# move when pricing does. These two tests are the exception — they check the *real* configured rate,
# because the defect Phase 0 found was not in the arithmetic. The arithmetic was correct and the rate
# was wrong by about 100×, the module docstring said so and called it "a pricing question flagged in
# the design document, not a bug here", and no test looked at the number.
#
# Phase 3 redenominated both settings from pre-multiplier tokens into usage units, so the *bands* here
# moved by 50× while the money did not. That is the point of the change: a unit is $0.0001 of measured
# cost, so the band below can be derived from a provider price list rather than from a chain of
# reasoning about what a token was worth. The rate is now checkable in one step.


def test_a_voice_minute_is_priced_near_what_a_voice_minute_costs():
    """The rate must stay within an order of magnitude of the provider bill.

    A conversational minute of `gemini-3.1-flash-live-preview` costs about **$0.023** — roughly
    $0.005/min of audio in and $0.018/min out. At $0.0001 per unit that is ~230 units, and the
    configured 200 is deliberately a little under: err towards the learner, keep the magnitude.

    Deliberately a loose band rather than an equality, to catch a rate that has drifted an order of
    magnitude from its cost basis — which is what happened when it stood at 100 pre-multiplier tokens,
    or about $0.0004 of cost for $0.023 of service — rather than to pin a figure Google will move.
    """
    from src.config import get_settings
    from src.domains.billing.services.credit_consumption_service import units_for_usd

    rate = float(get_settings().GEMINI_LIVE_UNITS_PER_MINUTE)
    measured = units_for_usd(0.023)
    assert measured / 3 <= rate <= measured * 3, (
        f"GEMINI_LIVE_UNITS_PER_MINUTE is {rate}, and a voice minute measures {measured} units "
        f"(~$0.023). If Google's pricing moved, move the figure and say so; if this drifted, it is "
        f"the 2026-09-01 defect returning."
    )


def test_the_session_floor_is_at_least_a_minute_of_voice():
    """A floor below one minute is not a floor.

    `GEMINI_LIVE_MIN_SESSION_UNITS` was once 500 against a rate of 100 — five minutes, which was
    coherent. When the rate was corrected to 10 000 the same 500 became three seconds, so the
    wall-clock minimum charge would have silently stopped existing had it not been moved with it. That
    is the coupling this test exists to hold: the floor is meaningless except in terms of the rate. It
    doubles as the pre-start availability check, so too low also means a learner can begin a session
    they cannot afford a minute of.
    """
    from src.config import get_settings

    cfg = get_settings()
    assert cfg.GEMINI_LIVE_MIN_SESSION_UNITS >= cfg.GEMINI_LIVE_UNITS_PER_MINUTE


# ---------------------------------------------------------------------------
# Billing mode
# ---------------------------------------------------------------------------


def test_free_tier_is_billed_for_the_whole_connection():
    assert billing.billing_mode_for_tier("FREE") == billing.BILLING_WALL_CLOCK


def test_a_missing_tier_is_treated_as_free():
    """Standby is a paid perk, so an unknown tier must not be given it by default."""
    assert billing.billing_mode_for_tier(None) == billing.BILLING_WALL_CLOCK


@pytest.mark.parametrize("tier", ["PLUS", "PRO", "anything-not-free"])
def test_paid_tiers_are_billed_only_while_audio_is_flowing(tier):
    assert billing.billing_mode_for_tier(tier) == billing.BILLING_ACTIVE_AUDIO


# ---------------------------------------------------------------------------
# Accrual
# ---------------------------------------------------------------------------


def test_accrual_is_proportional_to_billable_time(settings):
    assert billing.units_from_billable_seconds_raw(60.0) == 100
    assert billing.units_from_billable_seconds_raw(30.0) == 50


def test_accrual_truncates_rather_than_rounding_up(settings):
    """A partial credit is not charged until it is whole, so ticks cannot outrun the time they bill."""
    assert billing.units_from_billable_seconds_raw(0.5) == 0
    assert billing.units_from_billable_seconds_raw(1.2) == 2


def test_accrual_never_goes_negative(settings):
    assert billing.units_from_billable_seconds_raw(-10.0) == 0


def test_accrual_has_no_session_floor(settings):
    """The floor belongs to settlement only. Applied during accrual it would charge it every tick."""
    assert billing.units_from_billable_seconds_raw(1.0) < billing.min_session_units()


# ---------------------------------------------------------------------------
# Settlement
# ---------------------------------------------------------------------------


def test_a_short_free_session_still_costs_the_session_minimum(settings):
    total = billing.units_total_final_settlement(10.0, billing.BILLING_WALL_CLOCK)
    assert total == 500


def test_a_long_free_session_costs_more_than_the_minimum(settings):
    total = billing.units_total_final_settlement(600.0, billing.BILLING_WALL_CLOCK)
    assert total == 1000


def test_a_short_paid_session_is_not_floored(settings):
    """A paid learner who spoke for ten seconds genuinely cost ten seconds."""
    total = billing.units_total_final_settlement(10.0, billing.BILLING_ACTIVE_AUDIO)
    assert total == 16


def test_settlement_of_no_time_is_free_for_paid_and_floored_for_free(settings):
    assert billing.units_total_final_settlement(0.0, billing.BILLING_ACTIVE_AUDIO) == 0
    assert billing.units_total_final_settlement(0.0, billing.BILLING_WALL_CLOCK) == 500


def test_settlement_never_undercuts_what_was_already_charged(settings):
    """The property the loop depends on: the running total can only be caught up to, never exceeded.

    `settle` charges `total - consumed`, so if accrual could ever exceed settlement for the same seconds,
    the difference would be silently refunded — and there is no refund path.
    """
    for seconds in (0.0, 1.0, 7.5, 61.0, 3600.0):
        for mode in (billing.BILLING_WALL_CLOCK, billing.BILLING_ACTIVE_AUDIO):
            accrued = billing.units_from_billable_seconds_raw(seconds)
            assert billing.units_total_final_settlement(seconds, mode) >= accrued


# ---------------------------------------------------------------------------
# Bridge state
# ---------------------------------------------------------------------------


def test_a_fresh_bridge_has_nothing_to_settle():
    snapshot = BridgeState(billing_mode=billing.BILLING_WALL_CLOCK).snapshot()
    assert snapshot.billing_started is False
    assert snapshot.billable_seconds == 0.0
    assert snapshot.consumed_credits == 0


def test_the_snapshot_carries_what_settlement_needs():
    state = BridgeState(billing_mode=billing.BILLING_ACTIVE_AUDIO)
    state.billing_started = True
    state.billable_seconds = 42.0
    state.consumed_credits = 60
    snapshot = state.snapshot()
    assert (
        snapshot.billing_mode,
        snapshot.billable_seconds,
        snapshot.consumed_credits,
    ) == (
        billing.BILLING_ACTIVE_AUDIO,
        42.0,
        60,
    )


# ---------------------------------------------------------------------------
# Tool exposure
# ---------------------------------------------------------------------------


def test_a_session_without_a_topic_gets_no_tools():
    """The original fell back to the entire agentic toolset here, which could create and delete a learner's
    content from a socket with no client handling for the result."""
    assert study_tools_for(None) is None


def test_a_session_with_a_topic_gets_only_study_tools():
    groups = study_tools_for("topic-1")
    assert groups and "functionDeclarations" not in groups[0]
    names = {d["name"] for d in groups[0]["function_declarations"]}
    assert names == {"study_show_visual", "complete_topic_and_continue"}


# ---------------------------------------------------------------------------
# Session store, in its Redis-unavailable mode
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    """Force the in-process path, and clear it between tests."""
    monkeypatch.setattr(session_store.cache, "_connected", False, raising=False)
    session_store._local_sessions.clear()
    session_store._local_user_session.clear()
    yield
    session_store._local_sessions.clear()
    session_store._local_user_session.clear()


@pytest.mark.asyncio
async def test_a_session_can_be_read_back_by_id():
    created = await session_store.create("user-1", system_instruction="brief", topic_id="t1")
    found = await session_store.get(created.session_id)
    assert found is not None
    assert (found.user_id, found.topic_id, found.system_instruction) == (
        "user-1",
        "t1",
        "brief",
    )


@pytest.mark.asyncio
async def test_starting_a_second_session_closes_the_first():
    """One voice, one session. Two live sessions would bill for audio the learner cannot hear."""
    first = await session_store.create("user-1", system_instruction="a")
    second = await session_store.create("user-1", system_instruction="b")

    assert await session_store.get(first.session_id) is None
    assert [s.session_id for s in await session_store.list_for_user("user-1")] == [
        second.session_id
    ]


@pytest.mark.asyncio
async def test_one_learners_session_does_not_appear_for_another():
    await session_store.create("user-1", system_instruction="a")
    mine = await session_store.create("user-2", system_instruction="b")
    assert [s.session_id for s in await session_store.list_for_user("user-2")] == [mine.session_id]


@pytest.mark.asyncio
async def test_stopping_a_session_forgets_it_entirely():
    created = await session_store.create("user-1", system_instruction="a")
    await session_store.delete(created.session_id)
    assert await session_store.get(created.session_id) is None
    assert await session_store.list_for_user("user-1") == []


@pytest.mark.asyncio
async def test_update_context_only_overwrites_what_it_was_given():
    """A frame carrying a topic but no course must not blank the course."""
    created = await session_store.create(
        "user-1", system_instruction="a", course_id="c1", topic_id="t1"
    )
    updated = await session_store.update_context(created.session_id, topic_id="t2")
    assert updated is not None
    assert (updated.course_id, updated.topic_id) == ("c1", "t2")


@pytest.mark.asyncio
async def test_update_context_on_an_unknown_session_reports_it():
    assert await session_store.update_context("not-a-session", topic_id="t1") is None


@pytest.mark.asyncio
async def test_the_session_floor_can_only_be_claimed_once():
    """A dropped socket reconnects into the same session, and each attempt settles separately.

    Without this the wall-clock minimum would be charged per socket rather than per sitting, so a FREE
    learner on a flaky connection pays it five times for one conversation — the web client retries five
    times.
    """
    created = await session_store.create("user-1", system_instruction="a")
    assert await session_store.claim_session_floor(created.session_id) is True
    assert await session_store.claim_session_floor(created.session_id) is False
    assert await session_store.claim_session_floor(created.session_id) is False


@pytest.mark.asyncio
async def test_an_unknown_session_is_charged_the_floor():
    """Failing open here: a learner who calls stop before the relay unwinds must not get a free session."""
    assert await session_store.claim_session_floor("expired-or-never-existed") is True


@pytest.mark.asyncio
async def test_each_learners_floor_is_claimed_independently():
    first = await session_store.create("user-1", system_instruction="a")
    second = await session_store.create("user-2", system_instruction="b")
    assert await session_store.claim_session_floor(first.session_id) is True
    assert await session_store.claim_session_floor(second.session_id) is True


@pytest.mark.asyncio
async def test_a_session_starts_with_no_note():
    """Nothing is written unless the learner asks, so a fresh session has no note attached."""
    created = await session_store.create("user-1", system_instruction="a", topic_id="t1")
    assert created.note_id is None


@pytest.mark.asyncio
async def test_note_taking_starts_off():
    """The default that makes the rest of this defensible.

    A session buffers the transcript in memory because it needs it to run. With note-taking off, nothing is
    ever written from that buffer — which is the difference between this and the automatic writer that used
    to summarise a learner's conversation into their notes without asking.
    """
    created = await session_store.create("user-1", system_instruction="a", topic_id="t1")
    assert created.note_taking is False
    assert created.turns_at_last_note == 0


@pytest.mark.asyncio
async def test_note_taking_can_be_switched_on_and_off_again():
    created = await session_store.create("user-1", system_instruction="a", topic_id="t1")

    on = await session_store.set_note_taking(created.session_id, True)
    assert on is not None and on.note_taking is True
    assert (await session_store.get(created.session_id)).note_taking is True

    off = await session_store.set_note_taking(created.session_id, False)
    assert off is not None and off.note_taking is False
    assert (await session_store.get(created.session_id)).note_taking is False


@pytest.mark.asyncio
async def test_toggling_note_taking_on_an_unknown_session_reports_it():
    """`None` rather than a silent success.

    A learner who pressed Take note and got no acknowledgement will believe the conversation is being kept
    and expect a note that was never going to be written. Failing visibly is the lesser harm.
    """
    assert await session_store.set_note_taking("not-a-session", True) is None


@pytest.mark.asyncio
async def test_note_taking_survives_a_reconnect():
    """The toggle lives on the record, not on the socket.

    The web client re-sends `start_session` with the same id after a dropped connection, and the note is
    written at teardown of whichever relay ends the sitting. If the flag lived in the socket's memory, a
    learner whose connection blipped would silently stop having their session written up.
    """
    created = await session_store.create("user-1", system_instruction="a", topic_id="t1")
    await session_store.set_note_taking(created.session_id, True)

    reloaded = await session_store.get(created.session_id)
    assert reloaded is not None and reloaded.note_taking is True


@pytest.mark.asyncio
async def test_a_session_remembers_the_note_it_was_saved_to():
    created = await session_store.create("user-1", system_instruction="a", topic_id="t1")
    await session_store.remember_note(created.session_id, "note-9", turns=12)
    reloaded = await session_store.get(created.session_id)
    assert reloaded is not None and reloaded.note_id == "note-9"
    # The conversation length at the time of writing, which is what lets a later pass tell "there is more to
    # say" from "nothing has happened since" — and so refuse to charge twice for one note.
    assert reloaded.turns_at_last_note == 12


@pytest.mark.asyncio
async def test_remembering_a_note_on_an_unknown_session_is_a_no_op():
    await session_store.remember_note("not-a-session", "note-9")
    assert await session_store.get("not-a-session") is None
