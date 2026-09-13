"""Programme email. What a tester is told, and when.

**These are transactional and deliberately bypass the notification orchestrator**, the same way
``identity/emails.py`` does and for a closely related reason. A tester who has switched off learning
email has switched off study reminders; they have not asked to stop being told that ₦2,000 landed in
their balance or that a bank transfer went out. Routing an outcome-of-work message through engagement
policy would let a preference about revision nudges swallow a statement about somebody's money.

The one exception is the season-opening announcement, which genuinely *is* marketing to a warm list.
That goes through the orchestrator as ``bug_hunt.season_open`` under ``OPERATIONS``, so it is
consent-gated, unsubscribable, quiet-hours aware and retried. See ``services/announce_service.py``.

**Nothing here raises.** Every function swallows its own failures and returns ``bool``. An email
provider being down must never turn a successful triage into a 500, because the grading has already
committed and the money has already moved: failing the request would tell the triager to try again and
produce a second award. The return value exists so a caller *could* dedupe on it, and so a test can
assert a send was attempted, but no caller currently branches on it.

**No amount is formatted anywhere but ``_naira``.** A divide-by-100 written at six call sites is one
written wrongly at one of them, and here that is the difference between emailing somebody ₦2,000 and
₦200,000.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging

from src.config import get_settings
from src.shared.infrastructure.email import send_templated_email
from src.shared.infrastructure.email_evidence import TransactionalEvidence

logger = logging.getLogger(__name__)


def _base_url() -> str:
    """Where the participant app lives. Resolved per send, so a test can move it."""
    settings = get_settings()
    return (settings.BUG_HUNT_BASE_URL or "https://issues.maigie.com").rstrip("/")


def _url(path: str) -> str:
    return f"{_base_url()}{path}"


def _naira(kobo: int | None) -> str:
    """``200000`` becomes ``"₦2,000"``. The only place kobo is converted in this module."""
    if kobo is None:
        return "₦0"
    naira = kobo / 100
    if naira == int(naira):
        return f"₦{int(naira):,}"
    return f"₦{naira:,.2f}"


def _evidence(purpose: str) -> TransactionalEvidence:
    """One evidence row per send.

    ``OPERATIONS`` rather than ``BILLING``: no invoice exists and nobody was charged. The purpose is
    prefixed so every Bug Hunt message is one query away when somebody asks whether a tester was ever
    told their payout went out.
    """
    return TransactionalEvidence(message_class="OPERATIONS", purpose=f"bug_hunt:{purpose}")


async def _send(
    template: str,
    *,
    to_email: str,
    subject: str,
    fallback_text: str,
    purpose: str,
    ref_id: str,
    **data: object,
) -> bool:
    """Render and send, swallowing everything. Returns whether it was handed to a provider."""
    if not to_email:
        logger.warning("bug_hunt: no address for %s; skipping", purpose)
        return False
    try:
        await send_templated_email(
            template,
            to_email=to_email,
            subject=subject,
            fallback_text=fallback_text,
            ref_id=ref_id,
            evidence=_evidence(purpose),
            **data,
        )
    except Exception:
        # Logged at exception level with the purpose, because "the tester was never told" is a real
        # operational fact somebody may need to find later, and the evidence row already records the
        # provider's own error.
        logger.exception("bug_hunt: %s email to %s failed", purpose, to_email)
        return False
    return True


# ===========================================================================
# Applications
# ===========================================================================


async def application_approved(
    *,
    to_email: str,
    name: str | None,
    season_name: str,
    ends_at: str,
    daily_limit: int,
    cap_kobo: int,
) -> bool:
    """Tell an applicant they are in.

    Says explicitly that the application's own finding is still being graded separately. Without that
    line, an approval followed hours later by a grading email reads as two contradictory decisions.
    """
    return await _send(
        "bug_hunt_application_approved",
        to_email=to_email,
        subject=f"You're in the {season_name} Bug Hunt",
        fallback_text=(
            f"Your application to {season_name} of the Maigie Bug Hunt was accepted. "
            f"File a finding: {_url('/submit')}"
        ),
        purpose="application_approved",
        ref_id=f"bug-hunt-approved-{to_email}",
        name=name,
        season_name=season_name,
        ends_at=ends_at,
        daily_limit=daily_limit,
        cap=_naira(cap_kobo),
        submit_url=_url("/submit"),
        rules_url=_url("/rules"),
    )


async def application_rejected(
    *,
    to_email: str,
    name: str | None,
    season_name: str,
    reason: str,
    can_retry: bool,
) -> bool:
    """Turn an applicant down, with the reason and whether they have a retry left.

    The reason travels verbatim from the triager. A rejection without one is refused at the service
    boundary, so there is no branch here for a missing reason: if this template ever renders an empty
    one, the constraint upstream has been broken and a vague email is the least of the problems.
    """
    return await _send(
        "bug_hunt_application_rejected",
        to_email=to_email,
        subject=f"Your {season_name} Bug Hunt application",
        fallback_text=f"We did not accept your {season_name} Bug Hunt application. Reason: {reason}",
        purpose="application_rejected",
        ref_id=f"bug-hunt-rejected-{to_email}",
        name=name,
        season_name=season_name,
        reason=reason,
        can_retry=can_retry,
        apply_url=_url("/apply"),
        rules_url=_url("/rules"),
    )


# ===========================================================================
# Findings
# ===========================================================================

#: Headline and explanation per non-accepted outcome.
#:
#: ``duplicate`` and ``known_issue`` are separated here as carefully as they are in the UI. Both pay
#: nothing, and the difference is whose fault it is: being second is another tester's speed, while a
#: known issue is us admitting we knew and had not fixed it. One shared "already reported" message
#: would blame the reporter for our backlog.
_DECLINED_COPY: dict[str, tuple[str, str]] = {
    "rejected": (
        "We did not accept this finding",
        "Most findings we turn down are ones we could not reproduce from the steps given, or ones "
        "where the app behaved as it was designed to. If it is the second and you disagree with the "
        "design, send it again as feedback, which has its own rate.",
    ),
    "duplicate": (
        "Someone reported this first",
        "Only the first report of a bug pays, so this one does not. It is a real bug and you were "
        "right about it. You were just second, and that is timing rather than a judgement on your "
        "work.",
    ),
    "known_issue": (
        "We already knew about this one",
        "It is on our list and not yet fixed, which is our problem rather than yours. Known issues "
        "do not pay, and we would rather you knew that than wondered why the amount was zero.",
    ),
}


async def submission_accepted(
    *,
    to_email: str,
    name: str | None,
    title: str,
    grade: str,
    award_kobo: int,
    balance_kobo: int,
    public_response: str | None = None,
    blocked_reason: str | None = None,
) -> bool:
    """Tell a tester their finding was accepted, what it was graded, and what it paid.

    ``blocked_reason`` is the case where the grading committed but the award did not, which the
    two-transaction design in ``triage_service`` allows on purpose. The email still goes out and still
    says accepted, because it is: what changes is one paragraph saying the money is owed and being
    chased. Suppressing the email until the award landed would leave the tester with no word at all on
    the outcome, which is worse than an honest caveat.
    """
    return await _send(
        "bug_hunt_submission_accepted",
        to_email=to_email,
        subject=f"Accepted: {_naira(award_kobo)} for your finding",
        fallback_text=(
            f'We accepted your finding "{title}", graded {grade}, worth {_naira(award_kobo)}. '
            f"Your wallet: {_url('/wallet')}"
        ),
        purpose="submission_accepted",
        ref_id=f"bug-hunt-accepted-{to_email}",
        name=name,
        title=title,
        grade=grade,
        amount=_naira(award_kobo),
        balance=_naira(balance_kobo),
        response=(public_response or "").strip() or None,
        blocked=blocked_reason,
        wallet_url=_url("/wallet"),
    )


async def submission_declined(
    *,
    to_email: str,
    name: str | None,
    title: str,
    status: str,
    public_response: str | None = None,
) -> bool:
    """Tell a tester their finding does not pay, and which of the three reasons it was."""
    headline, explainer = _DECLINED_COPY.get(status, _DECLINED_COPY["rejected"])
    return await _send(
        "bug_hunt_submission_declined",
        to_email=to_email,
        subject=headline,
        fallback_text=f'{headline}: your finding "{title}". {explainer}',
        purpose=f"submission_{status}",
        ref_id=f"bug-hunt-declined-{to_email}",
        name=name,
        title=title,
        headline=headline,
        explainer=explainer,
        response=(public_response or "").strip() or None,
        dashboard_url=_url("/dashboard"),
    )


# ===========================================================================
# Cash
# ===========================================================================


async def withdrawal_approved(
    *,
    to_email: str,
    name: str | None,
    amount_kobo: int,
    bank_name: str | None,
    account_last4: str | None,
) -> bool:
    """Approval is not payment, and this email is careful to say so.

    It names the two working days and promises a second email with the reference, because the gap
    between approved and paid is where somebody starts wondering whether the programme is real.
    """
    return await _send(
        "bug_hunt_withdrawal_approved",
        to_email=to_email,
        subject=f"Your {_naira(amount_kobo)} transfer is approved",
        fallback_text=(
            f"Your request for {_naira(amount_kobo)} is approved. "
            "A person makes the transfer next, usually within two working days."
        ),
        purpose="withdrawal_approved",
        ref_id=f"bug-hunt-withdrawal-approved-{to_email}",
        name=name,
        amount=_naira(amount_kobo),
        bank_name=bank_name or "your bank",
        account_last4=account_last4 or "0000",
        wallet_url=_url("/wallet"),
    )


async def withdrawal_paid(
    *,
    to_email: str,
    name: str | None,
    amount_kobo: int,
    bank_name: str | None,
    account_last4: str | None,
    reference: str,
) -> bool:
    """The money is gone, and here is the reference.

    The reference is the whole point of this message. It is the one thing a tester can check against
    their own bank alert, which turns "they say they paid me" into something verifiable without
    anybody having to be trusted.
    """
    return await _send(
        "bug_hunt_withdrawal_paid",
        to_email=to_email,
        subject=f"Sent: {_naira(amount_kobo)} to your bank",
        fallback_text=(
            f"We have sent {_naira(amount_kobo)} to {bank_name or 'your bank'}. "
            f"Bank reference: {reference}"
        ),
        purpose="withdrawal_paid",
        ref_id=f"bug-hunt-withdrawal-paid-{to_email}",
        name=name,
        amount=_naira(amount_kobo),
        bank_name=bank_name or "your bank",
        account_last4=account_last4 or "0000",
        reference=reference,
        wallet_url=_url("/wallet"),
    )


__all__ = [
    "application_approved",
    "application_rejected",
    "submission_accepted",
    "submission_declined",
    "withdrawal_approved",
    "withdrawal_paid",
]
