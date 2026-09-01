"""Consent, timing, and evidence for notification email.

The failures worth guarding here are all quiet ones. Email that goes out after consent was
withdrawn is worse than email that fails, because nothing reports it and the learner's only
recourse is to stop trusting the settings screen. Email dropped without a record is equally
bad in the other direction: the ledger says nothing happened, so nobody can tell a suppressed
message from one that was never planned.

So these tests are about two questions. Was this send authorised *at the moment it left*, and
does the ledger say honestly what became of it?
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from src.config import Settings
from src.domains.notifications import email_dispatcher
from src.domains.notifications.email_delivery import EmailOutcome, address_reference


def _settings(**overrides: Any) -> Settings:
    base = {
        "NOTIFICATION_EMAIL_ENABLED": True,
        "NOTIFICATION_EMAIL_ROLLOUT_PERCENT": 100,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _policy(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "engagement_enabled": True,
        "timezone": "UTC",
        "timezone_source": "MANUAL",
        "quiet_hours_start": None,
        "quiet_hours_end": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _override(frequency: str = "IMMEDIATE", enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(enabled=enabled, frequency=frequency)


def _notification(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "id": "n1",
        "user_id": "u1",
        "type": "learning.study_session_reminder",
        "category": "LEARNING",
        "title": "Time to study",
        "body": "Your session starts soon.",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@dataclass
class FakeRepo:
    """Records what the dispatcher decided, so assertions read as ledger outcomes."""

    policy: Any = None
    legacy: Any = None
    override: Any = None
    recipient: tuple[str, str | None] | None = ("learner@example.com", "Ada")
    claimed: list[tuple[Any, Any]] | None = None
    suppressed: list[tuple[str, str]] | None = None
    deferred: list[tuple[str, datetime]] | None = None
    results: list[dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        self.policy = self.policy or _policy()
        self.legacy = (
            self.legacy if self.legacy is not None else SimpleNamespace(notifications=True)
        )
        self.override = self.override or _override()
        self.claimed = self.claimed if self.claimed is not None else []
        self.suppressed = []
        self.deferred = []
        self.results = []

    async def channel_policy(self, user_id, notification_type, category, channel):
        assert channel == "EMAIL"
        return {"policy": self.policy, "legacy": self.legacy, "override": self.override}

    async def email_recipient(self, user_id):
        return self.recipient

    async def claim_due_email_deliveries(self, *, limit, now):
        return self.claimed

    async def suppress_delivery(self, delivery_id, reason):
        self.suppressed.append((delivery_id, reason))

    async def defer_delivery(self, delivery_id, *, next_attempt_at):
        self.deferred.append((delivery_id, next_attempt_at))

    async def record_email_result(self, delivery_id, **values):
        self.results.append({"delivery_id": delivery_id, **values})


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch) -> FakeRepo:
    fake = FakeRepo()
    monkeypatch.setattr(email_dispatcher, "notification_repo", fake)
    return fake


def _use_settings(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr(email_dispatcher, "get_settings", lambda: settings)


NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)


class TestSendTimeAuthorisation:
    @pytest.mark.asyncio
    async def test_allows_an_immediate_consented_email(
        self, repo: FakeRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use_settings(monkeypatch, _settings())

        allowed, reason, deferred = await email_dispatcher._email_allowed(_notification(), NOW)

        assert (allowed, reason, deferred) == (True, None, None)

    @pytest.mark.asyncio
    async def test_the_channel_kill_switch_stops_everything(
        self, repo: FakeRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use_settings(monkeypatch, _settings(NOTIFICATION_EMAIL_ENABLED=False))

        allowed, reason, _ = await email_dispatcher._email_allowed(_notification(), NOW)

        assert allowed is False
        assert reason == "EMAIL_CHANNEL_DISABLED"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("policy", "legacy", "override", "expected"),
        [
            (_policy(engagement_enabled=False), None, None, "ENGAGEMENT_DISABLED"),
            (None, None, None, "ENGAGEMENT_DISABLED"),
            (_policy(), SimpleNamespace(notifications=False), None, "LEGACY_MASTER_DISABLED"),
            (_policy(), SimpleNamespace(notifications=True), False, "EMAIL_CONSENT_MISSING"),
            (
                _policy(),
                SimpleNamespace(notifications=True),
                _override(enabled=False, frequency="OFF"),
                "CHANNEL_DISABLED",
            ),
        ],
    )
    async def test_absent_or_withdrawn_consent_suppresses_rather_than_sends(
        self,
        repo: FakeRepo,
        monkeypatch: pytest.MonkeyPatch,
        policy: Any,
        legacy: Any,
        override: Any,
        expected: str,
    ) -> None:
        _use_settings(monkeypatch, _settings())
        repo.policy = policy
        repo.legacy = legacy
        # `False` distinguishes "no row at all" from "a row saying off".
        repo.override = None if override is False else override

        allowed, reason, deferred = await email_dispatcher._email_allowed(_notification(), NOW)

        # Suppressed, not deferred: none of these resolve by waiting.
        assert (allowed, reason, deferred) == (False, expected, None)

    @pytest.mark.asyncio
    async def test_a_weekly_preference_does_not_email_every_reminder(
        self, repo: FakeRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use_settings(monkeypatch, _settings())
        repo.override = _override(frequency="DIGEST")

        allowed, reason, _ = await email_dispatcher._email_allowed(_notification(), NOW)

        # Asking for a weekly email must never be read as "email me each of these".
        assert allowed is False
        assert reason == "DIGEST_NOT_SUPPORTED"

    @pytest.mark.asyncio
    async def test_a_weekly_preference_does_send_the_weekly_summary(
        self, repo: FakeRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use_settings(monkeypatch, _settings())
        repo.override = _override(frequency="DIGEST")

        allowed, _, _ = await email_dispatcher._email_allowed(
            _notification(type="progress.weekly_summary", category="PROGRESS"), NOW
        )

        # The summary *is* the weekly email, so a weekly preference is consent for it.
        assert allowed is True

    @pytest.mark.asyncio
    async def test_quiet_hours_defer_instead_of_dropping(
        self, repo: FakeRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use_settings(monkeypatch, _settings())
        repo.policy = _policy(quiet_hours_start="08:00", quiet_hours_end="20:00")

        allowed, reason, deferred = await email_dispatcher._email_allowed(_notification(), NOW)

        assert allowed is False
        assert reason == "QUIET_HOURS"
        # The learner still gets it, after the window — losing it would need the producer
        # to run again, which for a daily job means never.
        assert deferred is not None and deferred > NOW

    @pytest.mark.asyncio
    async def test_a_learner_outside_the_rollout_is_deferred_not_discarded(
        self, repo: FakeRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use_settings(monkeypatch, _settings(NOTIFICATION_EMAIL_ROLLOUT_PERCENT=0))

        allowed, reason, deferred = await email_dispatcher._email_allowed(_notification(), NOW)

        assert (allowed, reason) == (False, None)
        assert deferred is not None and deferred > NOW


class TestDispatchEvidence:
    @pytest.mark.asyncio
    async def test_acceptance_is_recorded_with_the_provider_and_its_id(
        self, repo: FakeRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use_settings(monkeypatch, _settings())
        delivery = SimpleNamespace(id="d1", attempt_count=1)
        repo.claimed = [(delivery, _notification())]

        async def fake_send(**kwargs: Any) -> EmailOutcome:
            assert kwargs["to_email"] == "learner@example.com"
            return EmailOutcome(
                accepted=True,
                provider="resend",
                provider_message_id="msg-1",
                retryable=False,
                error_code=None,
                error_detail=None,
                duration_ms=42,
            )

        monkeypatch.setattr(email_dispatcher, "send_notification_email", fake_send)

        assert await email_dispatcher.dispatch_due_email() == 1
        (result,) = repo.results
        assert result["accepted"] is True
        assert result["provider"] == "resend"
        assert result["provider_message_id"] == "msg-1"
        assert repo.suppressed == []

    @pytest.mark.asyncio
    async def test_a_transient_failure_keeps_a_retry_time(
        self, repo: FakeRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use_settings(monkeypatch, _settings())
        repo.claimed = [(SimpleNamespace(id="d1", attempt_count=1), _notification())]

        async def fake_send(**_: Any) -> EmailOutcome:
            return EmailOutcome(
                accepted=False,
                provider="smtp",
                provider_message_id=None,
                retryable=True,
                error_code="SMTP_ERROR",
                error_detail="connection reset",
                duration_ms=10,
            )

        monkeypatch.setattr(email_dispatcher, "send_notification_email", fake_send)

        await email_dispatcher.dispatch_due_email()
        (result,) = repo.results
        assert result["retryable"] is True
        assert result["next_attempt_at"] is not None

    @pytest.mark.asyncio
    async def test_an_unusable_address_is_suppressed_with_a_reason(
        self, repo: FakeRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use_settings(monkeypatch, _settings())
        repo.claimed = [(SimpleNamespace(id="d1", attempt_count=1), _notification())]
        repo.recipient = None

        async def fake_send(**_: Any) -> EmailOutcome:  # pragma: no cover - must not run
            raise AssertionError("no address was available; nothing should have been sent")

        monkeypatch.setattr(email_dispatcher, "send_notification_email", fake_send)

        await email_dispatcher.dispatch_due_email()
        assert repo.suppressed == [("d1", "NO_USABLE_ADDRESS")]
        assert repo.results == []

    @pytest.mark.asyncio
    async def test_a_withdrawn_consent_between_planning_and_sending_wins(
        self, repo: FakeRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use_settings(monkeypatch, _settings())
        repo.claimed = [(SimpleNamespace(id="d1", attempt_count=1), _notification())]
        repo.override = _override(enabled=False, frequency="OFF")

        async def fake_send(**_: Any) -> EmailOutcome:  # pragma: no cover - must not run
            raise AssertionError("consent was withdrawn; nothing should have been sent")

        monkeypatch.setattr(email_dispatcher, "send_notification_email", fake_send)

        await email_dispatcher.dispatch_due_email()
        assert repo.suppressed == [("d1", "CHANNEL_DISABLED")]


class TestRetryBackoff:
    def test_backoff_grows_and_stays_bounded(self) -> None:
        first = email_dispatcher._retry_at("d1", 1, NOW)
        second = email_dispatcher._retry_at("d1", 2, NOW)
        far = email_dispatcher._retry_at("d1", 9, NOW)

        assert NOW < first < second
        # Capped, so a long outage does not push a retry past any sane expiry.
        assert far - NOW <= timedelta(seconds=3600 + 3600 // 4)

    def test_two_deliveries_do_not_retry_in_lockstep(self) -> None:
        assert email_dispatcher._retry_at("d1", 3, NOW) != email_dispatcher._retry_at("d2", 3, NOW)


class TestAddressReference:
    def test_is_stable_case_insensitive_and_not_the_address(self) -> None:
        reference = address_reference(" Learner@Example.com ")

        assert reference == address_reference("learner@example.com")
        assert "learner" not in reference
        assert len(reference) == 64


class TestNotificationTemplate:
    """The notification email uses the shared shell, not a second layout of its own.

    It was first written as a standalone HTML document, copied from the pre-existing
    `schedule_reminder.html`. That is the older style in this folder: a full `<html>` with its
    own body styling, no preheader, and colours that do not survive a client forcing dark
    mode. Meanwhile auth mail — verification, welcome, password reset — extends `base.html`,
    which uses tables for structure, states every colour explicitly, and carries an inbox
    preview line.

    Two layouts is the actual defect, not either layout: the brand drifts silently, and a fix
    applied to one is invisible in the other. So this asserts the notification mail is built
    from the shared shell, and that the templates whose senders were removed stay removed
    rather than lingering as a template someone later "reuses".
    """

    def _render(self, **overrides: Any) -> tuple[str, str]:
        from src.shared.infrastructure.email import APP_NAME, _render

        data: dict[str, Any] = {
            "app_name": APP_NAME,
            "logo_url": "",
            "title": "Your week in review",
            "body": "Study time: 1.5 hours\nStudy sessions: 3",
            "name": "Ada",
            "action_url": "https://maigie.com/notifications?open=abc",
            "settings_url": "https://maigie.com/settings?tab=notifications",
            "category_reason": "you asked Maigie to email you about your progress",
        }
        data.update(overrides)
        return _render("notification", "fallback", **data)

    def test_it_is_built_from_the_shared_shell(self) -> None:
        html, _ = self._render()

        # Structure and colours that only `base.html` supplies.
        assert '<table role="presentation"' in html
        assert "#f4f4f7" in html
        assert "background-color:#4F46E5" in html
        # The standalone body styling it used to carry must not come back.
        assert "font-family: Helvetica, Arial, sans-serif; color: #333333" not in html

    def test_it_carries_the_content_every_surface_needs(self) -> None:
        html, text = self._render()

        for part in (html, text):
            assert "Your week in review" in part
            assert "Study sessions: 3" in part
            assert "notifications?open=abc" in part
            assert "settings?tab=notifications" in part
            assert "you asked Maigie to email you about your progress" in part

    def test_a_multi_line_body_keeps_its_lines(self) -> None:
        html, text = self._render()

        # A summary is a list of figures. Collapsed onto one line it is unreadable, and HTML
        # collapses whitespace by default.
        assert "white-space:pre-line" in html
        assert "Study time: 1.5 hours\nStudy sessions: 3" in text

    def test_the_greeting_is_optional(self) -> None:
        html, text = self._render(name=None)

        assert "Hi " not in html
        assert "Hi " not in text

    def test_the_replaced_templates_are_gone(self) -> None:
        from pathlib import Path

        from src.shared.infrastructure.email import TEMPLATE_FOLDER

        folder = Path(str(TEMPLATE_FOLDER))
        for stem in ("schedule_reminder", "weekly_tips"):
            for suffix in (".html", ".txt"):
                assert not (folder / f"{stem}{suffix}").exists(), (
                    f"{stem}{suffix} has no sender since the reminder and summary became "
                    "canonical notifications; leaving it invites a second layout back in"
                )
