"""Who may take part, checked in one place.

Every write in this domain routes through `require_participant` or one of the assertions below. That is
deliberate: eligibility here is a conjunction of four independent facts — a season is open, the account's
country is on that season's allowlist, the person is an approved participant *in that season*, and they
have accepted *that season's* terms — and any one of them checked in only some of the places is a hole.

Country is checked **on every write, against the current season's allowlist**, not once at application.
`User.country` is self-declared and mutable, and a season's allowlist can differ from the last one's, so
an approval granted under Season 1's rules is not a standing permit.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

from src.domains.identity.db_models import User

from ..db_models import BugHuntParticipant, BugHuntProgram
from ..exceptions import (
    CountryNotEligibleError,
    CountryNotSetError,
    NotApprovedError,
    TermsNotAcceptedError,
)
from . import program_service


def country_allowed(program: BugHuntProgram, country: str | None) -> bool:
    """Whether this country may take part in this season. Case-insensitive on the ISO alpha-2 code."""
    if not country:
        return False
    allowlist = {c.strip().upper() for c in (program.country_allowlist or [])}
    return country.strip().upper() in allowlist


def assert_country(program: BugHuntProgram, user: User) -> None:
    """Refuse an ineligible or unknown country, with two different refusals.

    Unknown and ineligible are separated because the remedies are opposite: an unset country is fixed by
    the tester in ten seconds through `PUT /users/me/country`, and telling a Nigerian they are ineligible
    because we never asked would turn away exactly the person the season is for.
    """
    if not user.country:
        raise CountryNotSetError()
    if not country_allowed(program, user.country):
        raise CountryNotEligibleError(
            country=user.country,
            allowed=sorted({c.strip().upper() for c in (program.country_allowlist or [])}),
        )


def assert_terms_accepted(program: BugHuntProgram, participant: BugHuntParticipant) -> None:
    """Refuse a participant who has not accepted the terms for *this* season.

    Hit mostly by carried-forward participants, who are `approved` but arrive with
    `acceptedRulesVersion` null — and by anyone still on an older version after the terms were revised
    mid-season. Its own refusal rather than a flavour of `NOT_APPROVED`, because the app must show a
    one-screen acknowledgement and not a waiting state or an application form.
    """
    accepted = participant.accepted_rules_version
    if accepted is None or accepted < program.rules_version:
        raise TermsNotAcceptedError(rules_version=program.rules_version)


def assert_approved(participant: BugHuntParticipant | None) -> BugHuntParticipant:
    """Refuse anyone who is not an approved participant, naming which of the four states they are in."""
    if participant is None or participant.status != "approved":
        raise NotApprovedError(participant_status=participant.status if participant else None)
    return participant


async def require_participant(user: User) -> tuple[BugHuntProgram, BugHuntParticipant]:
    """The full gate for a participant write: open season, eligible country, approved, terms accepted.

    Returns the season and the participation, because every caller needs both — the season for its
    reward matrix and limits, the participation for the row it is about to write. Returning them here
    means the caller does not re-fetch and cannot accidentally act on a *different* season than the one
    it was authorised against.

    Order matters. The season is resolved first, because with nothing open the answer is the same for
    everyone and should not depend on their country. Country comes before participation status so that a
    tester who has moved is told about the country rather than being told to apply to a season they are
    not eligible for.
    """
    program = await program_service.require_open()
    assert_country(program, user)
    participant = await program_service.participation(user_id=user.id, program_id=program.id)
    approved = assert_approved(participant)
    assert_terms_accepted(program, approved)
    return program, approved


async def require_eligible_applicant(user: User) -> BugHuntProgram:
    """The gate for *applying*: an open season and an eligible country, nothing about participation.

    Participation is deliberately not checked here. The application route needs to distinguish "you are
    already in this season" (a redirect to submitting) from "you were rejected and may retry once" (a
    form with a warning), and both of those are its own business rather than a generic refusal.
    """
    program = await program_service.require_open()
    assert_country(program, user)
    return program
