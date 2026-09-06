"""What a digest preference now means, and the two ways it could quietly go wrong.

Before this, asking for a weekly email got you one only for notification types that were already
periodic; everything else was recorded as unsupported and never sent. The preference was honoured
in the narrowest sense and ignored in the common one.

The failure modes worth testing are both about counting.

**Periods are the learner's.** A week bounded in UTC closes on a Sunday afternoon in Auckland and
summarises somebody else's week. Worse, a period containing a daylight-saving transition is not
168 hours, so stepping by a fixed duration silently drops or double-counts an hour at the edge.

**An item belongs to exactly one digest.** A notification created near a boundary, or a planner run
that retries, must not be summarised twice — a learner cannot tell one event from two, and the
digest becomes a thing they stop trusting. An empty digest is the mirror image: a summary saying
nothing happened teaches its reader to ignore the sender.
"""

from __future__ import annotations

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from dataclasses import dataclass, field  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from typing import Any  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

import pytest  # noqa: E402

from src.config import Settings  # noqa: E402
from src.domains.notifications import digest  # noqa: E402
from src.domains.notifications.taxonomy import NOTIFICATION_SPECS, notification_spec  # noqa: E402
from src.shared.time import LearnerTimezone  # noqa: E402


def _tz(name: str) -> LearnerTimezone:
    return LearnerTimezone(zone=ZoneInfo(name), name=name, is_known=True, source="MANUAL")


class TestPeriodArithmetic:
    def test_a_daily_period_is_the_previous_local_day(self) -> None:
        # 09:00 in Lagos on 1 September; the finished day is 31 August, local.
        window = digest.completed_period(
            "DAILY", datetime(2026, 9, 1, 8, tzinfo=UTC), _tz("Africa/Lagos"), digest_day_of_week=0
        )

        assert window is not None
        assert window.start == datetime(2026, 8, 30, 23, tzinfo=UTC)
        assert window.end == datetime(2026, 8, 31, 23, tzinfo=UTC)

    def test_it_summarises_the_finished_period_never_the_current_one(self) -> None:
        now = datetime(2026, 9, 1, 8, tzinfo=UTC)
        window = digest.completed_period("DAILY", now, _tz("Africa/Lagos"), digest_day_of_week=0)

        # Summarising a period still in progress would send a partial day and leave nothing to
        # say when it actually ended.
        assert window is not None
        assert window.end <= now

    def test_a_week_containing_a_dst_transition_is_not_168_hours(self) -> None:
        # Auckland springs forward on Sunday 27 September 2026.
        window = digest.completed_period(
            "WEEKLY",
            datetime(2026, 9, 30, 12, tzinfo=UTC),
            _tz("Pacific/Auckland"),
            digest_day_of_week=1,
        )

        assert window is not None
        hours = (window.end - window.start).total_seconds() / 3600
        # Stepping by a fixed 168 hours would have moved the boundary an hour off, so the last
        # hour of the week would be summarised twice or not at all.
        assert hours == 167

    def test_the_settings_day_convention_is_converted_not_assumed(self) -> None:
        """`digestDayOfWeek` counts 0 as Sunday; Python's `weekday()` counts 0 as Monday."""
        sunday_start = digest.completed_period(
            "WEEKLY", datetime(2026, 9, 30, 12, tzinfo=UTC), _tz("UTC"), digest_day_of_week=0
        )
        monday_start = digest.completed_period(
            "WEEKLY", datetime(2026, 9, 30, 12, tzinfo=UTC), _tz("UTC"), digest_day_of_week=1
        )

        assert sunday_start is not None and monday_start is not None
        assert sunday_start.start.weekday() == 6
        assert monday_start.start.weekday() == 0

    def test_an_unknown_period_produces_nothing(self) -> None:
        assert (
            digest.completed_period("HOURLY", datetime.now(UTC), _tz("UTC"), digest_day_of_week=0)
            is None
        )


class TestDigestTaxonomy:
    @pytest.mark.parametrize("digest_type", ["learning.digest", "progress.digest", "social.digest"])
    def test_a_digest_is_never_an_item_in_another_digest(self, digest_type: str) -> None:
        # Otherwise each period would summarise the previous summary, forever.
        assert notification_spec(digest_type).digestible is False

    @pytest.mark.parametrize("digest_type", ["learning.digest", "progress.digest", "social.digest"])
    def test_a_digest_does_not_buzz_a_phone(self, digest_type: str) -> None:
        spec = notification_spec(digest_type)

        # The point of a digest is to stop interrupting, so push is not even allowed.
        assert "MOBILE_PUSH" not in spec.allowed_channels
        assert set(spec.default_channels) == {"IN_APP", "EMAIL"}

    def test_every_digest_category_can_actually_collect_something(self) -> None:
        for settings_category, (_type, source_categories) in digest.DIGEST_CATEGORIES.items():
            collectable = [
                name
                for name, spec in NOTIFICATION_SPECS.items()
                if spec.digestible
                and spec.category in source_categories
                and "EMAIL" in spec.allowed_channels
            ]
            assert collectable, f"{settings_category} would always produce an empty digest"

    def test_product_updates_is_absent_because_it_has_nothing_to_digest(self) -> None:
        # Emitting an always-empty digest would be dead code pretending to be a feature.
        assert "PRODUCT_UPDATES" not in digest.DIGEST_CATEGORIES


class TestBodyRendering:
    def test_each_item_is_one_line_of_plain_text(self) -> None:
        body = digest.render_digest_body(
            [("Review due", "3 cards are ready"), ("Goal at risk", "Frontend transition")]
        )

        assert body.splitlines() == [
            "• Review due — 3 cards are ready",
            "• Goal at risk — Frontend transition",
        ]
        # Read by the notification centre and the email template alike, so no markup.
        assert "<" not in body

    def test_a_repeated_body_is_not_echoed_after_its_title(self) -> None:
        body = digest.render_digest_body([("Review due", "Review due")])

        assert body == "• Review due"

    def test_only_the_first_line_of_a_multi_line_body_is_used(self) -> None:
        body = digest.render_digest_body([("Weekly summary", "Study time: 2h\nSessions: 4")])

        # A digest of full bodies is not a digest.
        assert body == "• Weekly summary — Study time: 2h"


@dataclass
class FakeRepo:
    subscriptions: list[dict[str, Any]] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)
    claim_result: dict[str, Any] | None = None
    claimed: list[dict[str, Any]] = field(default_factory=list)
    attached: list[tuple[str, str]] = field(default_factory=list)

    async def digest_subscriptions(self, *, limit: int) -> list[dict[str, Any]]:
        return self.subscriptions

    async def digestible_notifications(self, user_id: str, **kwargs: Any) -> list[dict[str, Any]]:
        self.last_query = kwargs
        return self.items

    async def claim_digest(self, **values: Any) -> dict[str, Any] | None:
        self.claimed.append(values)
        return self.claim_result

    async def attach_digest_notification(self, digest_id: str, notification_id: str) -> None:
        self.attached.append((digest_id, notification_id))


def _policy(timezone: str = "UTC", day: int = 1) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        timezone=timezone, timezone_source="MANUAL", digest_day_of_week=day, engagement_enabled=True
    )


@pytest.fixture
def planner(monkeypatch: pytest.MonkeyPatch) -> FakeRepo:
    repo = FakeRepo(
        subscriptions=[
            {
                "user_id": "u1",
                "settings_category": "LEARNING",
                "digest_period": "WEEKLY",
                "policy": _policy(),
            }
        ],
        items=[{"id": "n1", "title": "Review due", "body": "3 cards"}],
        claim_result={"id": "dig-1", "itemCount": 1},
    )
    monkeypatch.setattr(digest, "notification_repo", repo)
    monkeypatch.setattr(
        digest,
        "get_settings",
        lambda: Settings(
            _env_file=None, NOTIFICATION_EMAIL_ENABLED=True, NOTIFICATION_EMAIL_ROLLOUT_PERCENT=100
        ),
    )
    return repo


@pytest.fixture
def created(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    async def fake_create(**values: Any) -> Any:
        from types import SimpleNamespace

        rows.append(values)
        return SimpleNamespace(id=f"notif-{len(rows)}")

    monkeypatch.setattr("src.domains.notifications.service.create_notification", fake_create)
    return rows


class TestPlanner:
    @pytest.mark.asyncio
    async def test_it_builds_one_digest_and_links_it(
        self, planner: FakeRepo, created: list[dict[str, Any]]
    ) -> None:
        summary = await digest.plan_due_digests(now=datetime(2026, 9, 30, 12, tzinfo=UTC))

        assert summary["created"] == 1
        assert created[0]["type"] == "learning.digest"
        assert created[0]["title"] == "Your learning this week"
        assert "Review due" in created[0]["body"]
        assert planner.attached == [("dig-1", "notif-1")]

    @pytest.mark.asyncio
    async def test_a_period_with_nothing_in_it_produces_no_digest(
        self, planner: FakeRepo, created: list[dict[str, Any]]
    ) -> None:
        planner.items = []

        summary = await digest.plan_due_digests(now=datetime(2026, 9, 30, 12, tzinfo=UTC))

        # A summary that says nothing happened teaches its reader to ignore the sender.
        assert (summary["created"], summary["skippedEmpty"]) == (0, 1)
        assert created == []
        assert planner.claimed == []

    @pytest.mark.asyncio
    async def test_an_already_summarised_period_is_not_summarised_again(
        self, planner: FakeRepo, created: list[dict[str, Any]]
    ) -> None:
        planner.claim_result = None

        summary = await digest.plan_due_digests(now=datetime(2026, 9, 30, 12, tzinfo=UTC))

        # The hourly cadence means most runs re-examine a period already handled; the unique
        # key is what stops the learner getting the same week twice.
        assert (summary["created"], summary["alreadySummarised"]) == (0, 1)
        assert created == []

    @pytest.mark.asyncio
    async def test_it_only_collects_types_whose_taxonomy_permits_email(
        self, planner: FakeRepo, created: list[dict[str, Any]]
    ) -> None:
        await digest.plan_due_digests(now=datetime(2026, 9, 30, 12, tzinfo=UTC))

        requested = planner.last_query["email_allowed_types"]
        # `learning.review_due` is digestible but email-forbidden; a digest must not smuggle it
        # into an inbox just because it was bundled.
        assert "learning.review_due" not in requested
        assert "learning.morning_schedule" in requested

    @pytest.mark.asyncio
    async def test_nothing_is_built_while_the_email_channel_is_off(
        self, planner: FakeRepo, created: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            digest,
            "get_settings",
            lambda: Settings(_env_file=None, NOTIFICATION_EMAIL_ENABLED=False),
        )

        summary = await digest.plan_due_digests(now=datetime(2026, 9, 30, 12, tzinfo=UTC))

        # A digest exists to be emailed. Building one anyway would create an in-app item the
        # learner never asked for and no email at all.
        assert summary["created"] == 0
        assert created == []

    @pytest.mark.asyncio
    async def test_a_learner_outside_the_email_cohort_gets_no_digest(
        self, planner: FakeRepo, created: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            digest,
            "get_settings",
            lambda: Settings(
                _env_file=None,
                NOTIFICATION_EMAIL_ENABLED=True,
                NOTIFICATION_EMAIL_ROLLOUT_PERCENT=0,
            ),
        )

        assert (await digest.plan_due_digests(now=datetime(2026, 9, 30, 12, tzinfo=UTC)))[
            "created"
        ] == 0

    @pytest.mark.asyncio
    async def test_an_unrecognised_settings_category_is_skipped(
        self, planner: FakeRepo, created: list[dict[str, Any]]
    ) -> None:
        planner.subscriptions = [
            {
                "user_id": "u1",
                "settings_category": "PRODUCT_UPDATES",
                "digest_period": "WEEKLY",
                "policy": _policy(),
            }
        ]

        assert (await digest.plan_due_digests(now=datetime(2026, 9, 30, 12, tzinfo=UTC)))[
            "created"
        ] == 0

    @pytest.mark.asyncio
    async def test_the_idempotency_key_names_the_category_period_and_start(
        self, planner: FakeRepo, created: list[dict[str, Any]]
    ) -> None:
        await digest.plan_due_digests(now=datetime(2026, 9, 30, 12, tzinfo=UTC))

        key = created[0]["idempotency_key"]
        assert key.startswith("digest:LEARNING:WEEKLY:")
        # Two categories closing the same week must not collide on one key.
        assert "LEARNING" in key and "WEEKLY" in key


class TestHighVolumeRouting:
    """The LLM cohort's digests are built on the heavy queue; everyone else is built inline.

    The point is that the bounded-but-slow model call never runs inside the serial hourly planner —
    at volume that is what makes the planner miss its window and starve the default queue. So an
    LLM-enabled learner is enqueued and *not* built inline, and the claim happens in the task, not
    here; a learner outside the cohort is built inline exactly as before.
    """

    def _llm_settings(self) -> Settings:
        return Settings(
            _env_file=None,
            NOTIFICATION_EMAIL_ENABLED=True,
            NOTIFICATION_EMAIL_ROLLOUT_PERCENT=100,
            NOTIFICATION_DIGEST_LLM_ENABLED=True,
            NOTIFICATION_DIGEST_LLM_ALLOWLIST=["u1"],
        )

    @pytest.mark.asyncio
    async def test_an_llm_learner_is_enqueued_not_built_inline(
        self, planner: FakeRepo, created: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(digest, "get_settings", self._llm_settings)
        calls: list[dict[str, Any]] = []
        monkeypatch.setattr(digest, "_enqueue_digest", lambda **kw: calls.append(kw) or True)

        summary = await digest.plan_due_digests(now=datetime(2026, 9, 30, 12, tzinfo=UTC))

        assert summary["enqueued"] == 1
        assert summary["created"] == 0
        assert created == [], "the planner must not build an LLM digest inline"
        assert planner.claimed == [], "the claim belongs to the heavy task, not the planner"
        assert calls[0]["user_id"] == "u1"
        assert calls[0]["timezone_name"] == "UTC"

    @pytest.mark.asyncio
    async def test_a_broker_failure_falls_back_to_inline(
        self, planner: FakeRepo, created: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(digest, "get_settings", self._llm_settings)
        # The broker is unreachable, so enqueue reports failure. The digest must still be built.
        monkeypatch.setattr(digest, "_enqueue_digest", lambda **kw: False)

        summary = await digest.plan_due_digests(now=datetime(2026, 9, 30, 12, tzinfo=UTC))

        assert summary["enqueued"] == 0
        assert summary["created"] == 1
        assert len(created) == 1

    @pytest.mark.asyncio
    async def test_the_heavy_entry_point_builds_the_digest(
        self, planner: FakeRepo, created: list[dict[str, Any]]
    ) -> None:
        # What the Celery task calls. It rebuilds the timezone from a name and claims + creates.
        # The `planner` fixture patches get_settings to email-on / digest-LLM-off, so this exercises
        # the build/claim/create path deterministically without reaching for a model.

        outcome = await digest.process_digest_for_learner(
            user_id="u1",
            settings_category="LEARNING",
            period="WEEKLY",
            timezone_name="UTC",
            digest_day_of_week=1,
        )

        assert outcome["outcome"] == "created"
        assert len(created) == 1
        assert planner.claimed, "the heavy path claims the period itself"
