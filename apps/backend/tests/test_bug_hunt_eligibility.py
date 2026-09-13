"""Who may take part: the four checks, and the refusals they raise.

Eligibility here is a conjunction of four independent facts — a season is open, the country is on *that
season's* allowlist, the person is an approved participant *in that season*, and they have accepted
*that season's* terms. Any one of them checked in only some of the places is a hole, so the assertions
live in one module and this exercises them directly, with plain objects and no database.

The codes matter as much as the refusals. The participant app renders a different screen for most of
them, and two of the pairs below are easy to collapse into one and wrong to collapse:

- "we do not know your country" versus "your country is not eligible" — opposite remedies.
- "you are not approved" versus "you owe an acknowledgement" — a returning tester who is shown an
  application form they already passed will assume the programme lost their record.

Run with: pytest tests/test_bug_hunt_eligibility.py -v
"""

from types import SimpleNamespace

import pytest

from src.domains.bug_hunt.exceptions import (
    CountryNotEligibleError,
    CountryNotSetError,
    NotApprovedError,
    TermsNotAcceptedError,
)
from src.domains.bug_hunt.services import eligibility_service as svc


def season(*, allowlist=("NG",), rules_version=1):
    return SimpleNamespace(
        id="prog_1",
        season_number=1,
        country_allowlist=list(allowlist),
        rules_version=rules_version,
    )


def user(country="NG"):
    return SimpleNamespace(id="user_1", country=country)


def participant(status="approved", accepted=1, carried_from=None):
    return SimpleNamespace(
        id="part_1",
        status=status,
        accepted_rules_version=accepted,
        carried_from_program_id=carried_from,
    )


class TestCountryAllowed:
    def test_an_allowlisted_country_passes(self):
        assert svc.country_allowed(season(), "NG") is True

    @pytest.mark.parametrize("variant", ["ng", "Ng", " ng ", "NG "])
    def test_the_check_is_case_and_whitespace_insensitive(self, variant):
        """`User.country` is self-declared through a client, so its exact casing is not ours to trust.
        A lowercase `ng` turning an eligible Nigerian away would be indistinguishable from a policy."""
        assert svc.country_allowed(season(), variant) is True

    def test_a_country_outside_the_allowlist_fails(self):
        assert svc.country_allowed(season(), "GH") is False

    @pytest.mark.parametrize("empty", [None, "", "   "])
    def test_an_absent_country_is_not_allowed(self, empty):
        assert svc.country_allowed(season(), empty) is False

    def test_a_widened_allowlist_needs_no_code_change(self):
        """The array is on the season row, so a second country is data.

        Asserted because it is the cheap half of widening the programme — the expensive half is that
        cash payouts are Nigerian bank transfers made by hand, which a config change does not solve.
        """
        assert svc.country_allowed(season(allowlist=("NG", "GH")), "GH") is True

    def test_an_empty_allowlist_admits_nobody(self):
        """Fail closed. A season misconfigured with no countries must refuse everyone rather than
        reading an empty list as "no restriction"."""
        assert svc.country_allowed(season(allowlist=()), "NG") is False


class TestAssertCountry:
    def test_an_eligible_user_passes_silently(self):
        svc.assert_country(season(), user("NG"))

    def test_an_unset_country_is_its_own_refusal(self):
        """Fixed by the tester in ten seconds via `PUT /users/me/country`. Telling a Nigerian they are
        ineligible because we never asked would turn away exactly the person the season is for."""
        with pytest.raises(CountryNotSetError) as e:
            svc.assert_country(season(), user(None))
        assert e.value.code == "COUNTRY_NOT_SET"
        assert e.value.status_code == 409

    def test_an_ineligible_country_names_what_is_allowed(self):
        """The message is the copy the tester reads, so it has to say which countries rather than just
        refusing."""
        with pytest.raises(CountryNotEligibleError) as e:
            svc.assert_country(season(), user("GH"))
        assert e.value.code == "COUNTRY_NOT_ELIGIBLE"
        assert e.value.status_code == 403
        assert "NG" in e.value.message
        assert e.value.country == "GH"

    def test_the_two_country_refusals_have_different_codes(self):
        """Collapsing them would send the fixable case to a dead-end screen."""
        assert CountryNotSetError().code != CountryNotEligibleError("GH", ["NG"]).code


class TestAssertApproved:
    def test_an_approved_participant_is_returned(self):
        row = participant()
        assert svc.assert_approved(row) is row

    @pytest.mark.parametrize("status", ["pending", "rejected", "suspended"])
    def test_every_other_status_is_refused_and_reported(self, status):
        """One code carrying the status, rather than four codes the client has to enumerate. The app
        routes on `participantStatus`: pending waits, rejected reads the reason, suspended reads
        something else."""
        with pytest.raises(NotApprovedError) as e:
            svc.assert_approved(participant(status=status))
        assert e.value.code == "NOT_APPROVED"
        assert e.value.participant_status == status

    def test_no_participation_at_all_is_refused_with_a_null_status(self):
        """Which is how the app knows to show the application form rather than a waiting state."""
        with pytest.raises(NotApprovedError) as e:
            svc.assert_approved(None)
        assert e.value.participant_status is None

    @pytest.mark.parametrize("status", [None, "pending", "rejected", "suspended"])
    def test_each_refusal_carries_its_own_message(self, status):
        with pytest.raises(NotApprovedError) as e:
            svc.assert_approved(participant(status=status) if status else None)
        assert e.value.message
        assert e.value.message != "You are not an approved participant."


class TestAssertTermsAccepted:
    def test_a_current_acceptance_passes(self):
        svc.assert_terms_accepted(season(rules_version=1), participant(accepted=1))

    def test_a_carried_forward_participant_owes_an_acknowledgement(self):
        """The case this check exists for.

        Carry-forward seeds them `approved` with no accepted version, deliberately: this season's
        amounts, dates and possibly country scope differ, and consent to Season 1 is not consent to
        Season 2.
        """
        with pytest.raises(TermsNotAcceptedError) as e:
            svc.assert_terms_accepted(
                season(rules_version=2), participant(accepted=None, carried_from="prog_0")
            )
        assert e.value.code == "TERMS_NOT_ACCEPTED"
        assert e.value.rules_version == 2

    def test_an_outdated_acceptance_is_refused(self):
        """Terms revised mid-season bump `rulesVersion`, and everyone re-accepts."""
        with pytest.raises(TermsNotAcceptedError):
            svc.assert_terms_accepted(season(rules_version=3), participant(accepted=2))

    def test_a_newer_acceptance_than_the_season_passes(self):
        """Defensive, and the right direction to be lenient in: if a version was rolled back, refusing
        a tester who already accepted something later would be punishing them for our edit."""
        svc.assert_terms_accepted(season(rules_version=1), participant(accepted=2))

    def test_it_is_not_a_flavour_of_not_approved(self):
        """Separate codes because the screens differ: an acknowledgement is one button, and being
        unapproved is a form or a wait."""
        assert TermsNotAcceptedError(1).code != NotApprovedError("pending").code
        assert TermsNotAcceptedError(1).status_code == 409
