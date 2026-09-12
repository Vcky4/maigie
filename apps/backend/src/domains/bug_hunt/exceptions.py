"""Bug Hunt — named refusals.

Every one of these carries a machine-readable `code`, because the participant app renders a *different
screen* for most of them: "no season is open" is a landing state with a date on it, "your country is
not eligible" is a dead end, and "you already have an open withdrawal" is a link to the one that
exists. A generic 403 would collapse all three into the same shrug.

They subclass `MaigieError`, so `maigie_error_handler` in `src/app.py` serialises them consistently
with the rest of the API — message, code, and a `detail` that is hidden in production.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

from fastapi import status

from src.shared.exceptions import MaigieError


class NoOpenSeasonError(MaigieError):
    """Nothing is open to apply to or submit against.

    A 409 rather than a 404: the programme exists, it is between seasons. The app has a state for this
    and the copy names the next date when there is one, which is why `next_starts_at` travels here
    rather than being fetched separately by a client that has just been refused.
    """

    def __init__(self, next_starts_at: str | None = None):
        super().__init__(
            message=(
                "No Bug Hunt season is open right now."
                if next_starts_at is None
                else "No Bug Hunt season is open right now. The next one is scheduled."
            ),
            status_code=status.HTTP_409_CONFLICT,
            code="NO_OPEN_SEASON",
            detail=f"next_starts_at={next_starts_at}" if next_starts_at else None,
        )
        self.next_starts_at = next_starts_at


class CountryNotSetError(MaigieError):
    """The account has no country, so eligibility cannot be decided.

    Separate from `CountryNotEligibleError` because the remedy differs: this one is fixed by the tester
    in ten seconds via `PUT /users/me/country`, and telling them they are ineligible when we simply do
    not know would turn an eligible Nigerian away.
    """

    def __init__(self) -> None:
        super().__init__(
            message="Tell us which country you are in before applying.",
            status_code=status.HTTP_409_CONFLICT,
            code="COUNTRY_NOT_SET",
        )


class CountryNotEligibleError(MaigieError):
    """The account's country is outside this season's allowlist."""

    def __init__(self, country: str, allowed: list[str]):
        allowed_text = ", ".join(allowed) if allowed else "no countries"
        super().__init__(
            message=f"This Bug Hunt season is open to {allowed_text} only.",
            status_code=status.HTTP_403_FORBIDDEN,
            code="COUNTRY_NOT_ELIGIBLE",
            detail=f"country={country} allowed={allowed_text}",
        )
        self.country = country
        self.allowed = allowed


class AlreadyParticipatingError(MaigieError):
    """The caller already has a participation in this season.

    Raised on the application route specifically, and it is the refusal that keeps a returning tester
    out of a form they should never see: a carried-forward participant is already `approved`, so their
    path is straight to submitting, not through an audition they passed last season.
    """

    def __init__(self, participant_status: str):
        super().__init__(
            message="You are already signed up for this season.",
            status_code=status.HTTP_409_CONFLICT,
            code="ALREADY_PARTICIPATING",
            detail=f"status={participant_status}",
        )
        self.participant_status = participant_status


class AttemptLimitReachedError(MaigieError):
    """A rejected applicant has used their one permitted retry."""

    def __init__(self) -> None:
        super().__init__(
            message="You have already reapplied once for this season.",
            status_code=status.HTTP_409_CONFLICT,
            code="ATTEMPT_LIMIT_REACHED",
        )


class NotApprovedError(MaigieError):
    """The caller is not an approved participant in the open season.

    Carries the status so the app can route: `pending` waits, `rejected` reads the reason and may
    retry, `suspended` reads a different message, and no participation at all goes to the application
    form. One code with a status beats four codes the client has to enumerate.
    """

    def __init__(self, participant_status: str | None):
        messages = {
            None: "Apply to this Bug Hunt season before submitting.",
            "pending": "Your application is still being reviewed.",
            "rejected": "Your application for this season was not accepted.",
            "suspended": "Your participation in this season has been suspended.",
        }
        super().__init__(
            message=messages.get(participant_status, "You are not an approved participant."),
            status_code=status.HTTP_403_FORBIDDEN,
            code="NOT_APPROVED",
            detail=f"status={participant_status}",
        )
        self.participant_status = participant_status


class TermsNotAcceptedError(MaigieError):
    """A returning participant has not accepted this season's terms yet.

    Its own refusal rather than a variant of `NotApprovedError`, because the tester *is* approved and
    the app must show a one-screen acknowledgement rather than an application form or a waiting state.
    """

    def __init__(self, rules_version: int):
        super().__init__(
            message="Accept this season's terms to carry on.",
            status_code=status.HTTP_409_CONFLICT,
            code="TERMS_NOT_ACCEPTED",
            detail=f"rules_version={rules_version}",
        )
        self.rules_version = rules_version


class SeasonStateError(MaigieError):
    """An admin asked for a season transition its current state does not permit."""

    def __init__(self, message: str, detail: str | None = None, code: str = "SEASON_STATE"):
        super().__init__(
            message=message,
            status_code=status.HTTP_409_CONFLICT,
            code=code,
            detail=detail,
        )


class ReapplyTooSoonError(MaigieError):
    """A rejected applicant is inside the 48-hour cooldown on their one retry.

    Carries the moment they may try again, because the alternative — "try again later" — invites them to
    keep pressing the button, and a rejected applicant pressing a button is the least useful traffic this
    programme can generate.
    """

    def __init__(self, ready_at: str):
        super().__init__(
            message="You can reapply after a short wait. Use it to find something different.",
            status_code=status.HTTP_409_CONFLICT,
            code="REAPPLY_TOO_SOON",
            detail=f"ready_at={ready_at}",
        )
        self.ready_at = ready_at


class SubmissionLimitReachedError(MaigieError):
    """The season's daily submission ceiling, reached.

    A **429**, not a 409: it is a rate limit, and a client should treat it as one — including honouring
    the retry time rather than looping. The limit is a published property of the season rather than abuse
    control, which is why it is counted from the database and holds when the cache is down.
    """

    def __init__(self, limit: int, retry_at: str):
        super().__init__(
            message=(
                f"You have filed {limit} findings in the last 24 hours, which is this season's limit. "
                "Quality is what pays here, not volume."
            ),
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code="SUBMISSION_LIMIT_REACHED",
            detail=f"limit={limit} retry_at={retry_at}",
        )
        self.limit = limit
        self.retry_at = retry_at
