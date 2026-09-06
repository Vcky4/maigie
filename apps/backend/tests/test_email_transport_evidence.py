"""Which providers are worth attempting, and what a transactional send leaves behind.

**The provider gate.** `SMTP_HOST` was set to `smtp.gmail.com` with no username or password. This
module always authenticates, so every single email spent about two seconds being refused with
`530 Authentication Required`, logged an error, and then succeeded through Resend. Nothing looked
broken — mail arrived — which is exactly why it survived: the cost was latency on every message
and an error log that taught its reader to skip past it. A provider that cannot authenticate is
not a fallback, it is a guaranteed round trip, so it is now skipped rather than tried.

**The evidence.** Auth and security mail bypasses the notification orchestrator on purpose: a
consent preference must not be able to lock someone out of their own account. But that left these
messages with no record anywhere, so "the reset code never arrived" could not be answered — a
message never attempted, one the provider refused, and one accepted and lost downstream all
looked identical. `OutboundMessage` distinguishes them, and stores no code, body, or address.
"""

from __future__ import annotations

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from typing import Any  # noqa: E402

import pytest  # noqa: E402

from src.config import settings  # noqa: E402
from src.shared.infrastructure import email as em  # noqa: E402
from src.shared.infrastructure import email_evidence  # noqa: E402
from src.shared.infrastructure.email_evidence import TransactionalEvidence  # noqa: E402


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch):
    """Both providers usable, with every attempt captured."""
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com", raising=False)
    monkeypatch.setattr(settings, "SMTP_USER", "mailer@example.com", raising=False)
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "secret", raising=False)
    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test", raising=False)
    monkeypatch.setattr(settings, "EMAIL_OUTBOUND_STRATEGY", "smtp_then_resend", raising=False)

    attempts: dict[str, int] = {"smtp": 0, "resend": 0}

    def fake_smtp(*_args: Any, **_kwargs: Any) -> None:
        attempts["smtp"] += 1

    async def fake_resend(**_kwargs: Any) -> str:
        attempts["resend"] += 1
        return "prov-msg-1"

    monkeypatch.setattr(em, "_send_multipart_email_sync", fake_smtp)
    monkeypatch.setattr(em, "_send_via_resend", fake_resend)
    return attempts


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture evidence rows instead of writing them, since this needs no database."""
    rows: list[dict[str, Any]] = []

    async def fake_record(**values: Any) -> None:
        rows.append(values)

    monkeypatch.setattr(em, "record_transactional_message", fake_record)
    return rows


class TestProviderUsability:
    def test_a_host_without_credentials_is_not_usable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "SMTP_HOST", "smtp.gmail.com", raising=False)
        monkeypatch.setattr(settings, "SMTP_USER", None, raising=False)
        monkeypatch.setattr(settings, "SMTP_PASSWORD", None, raising=False)

        # This is the live misconfiguration that cost two seconds on every email.
        assert em._smtp_usable() is False

    def test_a_fully_configured_host_is_usable(self, providers: dict[str, int]) -> None:
        assert em._smtp_usable() is True

    def test_the_unusable_provider_is_skipped_not_attempted(
        self, providers: dict[str, int], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "SMTP_USER", None, raising=False)
        monkeypatch.setattr(settings, "SMTP_PASSWORD", None, raising=False)

        import asyncio

        provider, message_id = asyncio.run(
            em._send_multipart_email("a@b.c", "Subject", "<p>x</p>", "x")
        )

        # Not merely "it still sent" — SMTP must not have been dialled at all.
        assert providers["smtp"] == 0
        assert (provider, message_id) == ("resend", "prov-msg-1")

    def test_transport_is_unconfigured_when_no_provider_could_send(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "SMTP_HOST", "smtp.gmail.com", raising=False)
        monkeypatch.setattr(settings, "SMTP_USER", None, raising=False)
        monkeypatch.setattr(settings, "SMTP_PASSWORD", None, raising=False)
        monkeypatch.setattr(settings, "RESEND_API_KEY", "", raising=False)

        # A host that cannot authenticate used to read as "configured", so callers believed
        # mail was being sent.
        assert em._email_transport_configured() is False

    def test_a_strategy_naming_only_an_unusable_provider_is_unconfigured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "SMTP_HOST", "", raising=False)
        monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test", raising=False)
        monkeypatch.setattr(settings, "EMAIL_OUTBOUND_STRATEGY", "smtp_only", raising=False)

        # Resend is usable but the strategy never reaches it.
        assert em._email_transport_configured() is False


class TestTransactionalEvidence:
    @pytest.mark.asyncio
    async def test_an_accepted_send_records_the_provider_and_its_id(
        self, providers: dict[str, int], recorded: list[dict[str, Any]]
    ) -> None:
        await em.send_transactional_email(
            "learner@example.com",
            "Reset your password",
            "<p>code</p>",
            evidence=TransactionalEvidence(message_class="SECURITY", purpose="password_reset"),
        )

        assert len(recorded) == 1
        row = recorded[0]
        assert row["status"] == "ACCEPTED"
        assert row["provider"] == "smtp"
        assert row["evidence"].purpose == "password_reset"
        assert row["duration_ms"] is not None

    @pytest.mark.asyncio
    async def test_a_failed_send_is_recorded_and_still_raises(
        self, providers: dict[str, int], recorded: list[dict[str, Any]], monkeypatch
    ) -> None:
        def boom(*_a: Any, **_k: Any) -> None:
            raise RuntimeError("smtp down")

        async def also_boom(**_k: Any) -> str:
            raise RuntimeError("resend down")

        monkeypatch.setattr(em, "_send_multipart_email_sync", boom)
        monkeypatch.setattr(em, "_send_via_resend", also_boom)

        # The caller decides whether an unsendable transactional message is fatal; auditing it
        # must not change that decision.
        with pytest.raises(RuntimeError):
            await em.send_transactional_email(
                "learner@example.com",
                "Reset your password",
                "<p>code</p>",
                evidence=TransactionalEvidence(message_class="SECURITY", purpose="password_reset"),
            )

        assert [row["status"] for row in recorded] == ["FAILED"]
        assert recorded[0]["error_detail"]

    @pytest.mark.asyncio
    async def test_no_configured_provider_is_recorded_as_skipped(
        self, recorded: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "SMTP_HOST", "", raising=False)
        monkeypatch.setattr(settings, "RESEND_API_KEY", "", raising=False)

        await em.send_transactional_email(
            "learner@example.com",
            "Verify your email",
            "<p>code</p>",
            evidence=TransactionalEvidence(message_class="AUTH", purpose="verification"),
        )

        # "We never tried" and "the provider refused" are different answers to the same
        # question, so silence here would be the wrong record.
        assert [row["status"] for row in recorded] == ["SKIPPED"]
        assert recorded[0]["error_code"] == "NO_PROVIDER_CONFIGURED"

    @pytest.mark.asyncio
    async def test_a_send_without_evidence_records_nothing(
        self, providers: dict[str, int], recorded: list[dict[str, Any]]
    ) -> None:
        await em.send_transactional_email("learner@example.com", "Subject", "<p>x</p>")

        # Notification email passes no evidence because `NotificationDelivery` already holds
        # its record; a second row would be a second version of the truth.
        assert recorded == []

    @pytest.mark.asyncio
    async def test_templated_auth_mail_records_evidence(
        self, providers: dict[str, int], recorded: list[dict[str, Any]]
    ) -> None:
        from src.domains.identity import emails

        await emails.send_verification_email("learner@example.com", "123456", name="Ada")

        assert len(recorded) == 1
        assert recorded[0]["evidence"].message_class == "AUTH"
        assert recorded[0]["evidence"].purpose == "verification"

    @pytest.mark.asyncio
    async def test_password_reset_is_classed_as_security_not_auth(
        self, providers: dict[str, int], recorded: list[dict[str, Any]]
    ) -> None:
        from src.domains.identity import emails

        await emails.send_password_reset_email("learner@example.com", "654321")

        # Kept separate so security mail can be audited and retained on its own terms.
        assert recorded[0]["evidence"].message_class == "SECURITY"


class TestEvidenceNeverBreaksASend:
    @pytest.mark.asyncio
    async def test_a_failing_recorder_does_not_fail_the_email(
        self, providers: dict[str, int], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def exploding_factory() -> Any:
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(
            "src.shared.database.get_session_factory", exploding_factory, raising=False
        )

        # A password reset that fails because its audit row could not be written would be a
        # worse outcome than a reset with no audit row.
        await email_evidence.record_transactional_message(
            evidence=TransactionalEvidence(message_class="SECURITY", purpose="password_reset"),
            to_email="learner@example.com",
            status="ACCEPTED",
        )

    def test_it_registers_the_foreign_key_target_before_flushing(self) -> None:
        """`OutboundMessage.userId` points at `User.id`, so both mappers must be registered.

        Importing only the notifications models raises `NoReferencedTableError` at flush time.
        That is invisible in a request, where identity models are already imported, and it broke
        on a worker-invoked path where nothing had pulled them in — which is exactly where auth
        mail is sent from. The recorder swallows its own errors, so the symptom was a silently
        missing audit row rather than a visible failure. Found by sending a real email and
        finding no row.

        Asserted against the source rather than at runtime: reproducing it requires unloading the
        model modules, and re-importing them redefines `User` on the shared metadata, so the test
        would fail for a reason that has nothing to do with the behaviour being checked.
        """
        import inspect

        source = inspect.getsource(email_evidence.record_transactional_message)

        assert "from src.domains.identity.db_models import User" in source, (
            "the identity models must be imported alongside OutboundMessage so the userId "
            "foreign key can resolve on a worker path"
        )

    def test_the_address_is_hashed_the_same_way_the_ledger_hashes_it(self) -> None:
        from src.domains.notifications.email_delivery import address_reference

        # The two must agree, or an operator cannot join a transactional record to a
        # suppression for the same mailbox.
        assert email_evidence.address_hash(" Learner@Example.com ") == address_reference(
            "learner@example.com"
        )
