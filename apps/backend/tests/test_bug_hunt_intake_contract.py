"""The intake surface's contract: what a tester may send, and what comes back.

Schema-level, so no database. Two things are asserted here that a behavioural test cannot reach as
directly:

- **`SubmissionView` has no `adminNotes`.** A triager's private note reaching the person it is about is
  the kind of leak that happens through a generic serialiser, and the defence is that the field does not
  exist on the response model at all. Worth pinning, because `from_attributes` would be an easy
  "simplification" later.
- **`SubmissionFields` has no `category`, `severity` or `status`.** A submitter who could set their own
  severity could set their own payment. The service ignores unknown keys; this makes the *contract* say so,
  which is what a client author reads.

Run with: pytest tests/test_bug_hunt_intake_contract.py -v
"""

import pytest

from src.app import create_app
from src.domains.bug_hunt import attachments
from src.domains.bug_hunt.services import submission_service

PREFIX = "/api/v1/bug-hunt"


@pytest.fixture(scope="module")
def app():
    return create_app()


@pytest.fixture(scope="module")
def schema(app) -> dict:
    return app.openapi()


class TestTheIntakeSurfaceIsMounted:
    @pytest.mark.parametrize(
        "path",
        [
            f"{PREFIX}/applications",
            f"{PREFIX}/terms-acceptance",
            f"{PREFIX}/submissions",
            f"{PREFIX}/submissions/{{submission_id}}",
            f"{PREFIX}/submissions/{{submission_id}}/attachments",
        ],
    )
    def test_endpoint_is_mounted(self, path, app):
        assert path in {route.path for route in app.routes}

    @pytest.mark.parametrize(
        ("path", "method"),
        [
            (f"{PREFIX}/applications", "post"),
            (f"{PREFIX}/terms-acceptance", "post"),
            (f"{PREFIX}/submissions", "post"),
            (f"{PREFIX}/submissions", "get"),
            (f"{PREFIX}/submissions/{{submission_id}}", "get"),
            (f"{PREFIX}/submissions/{{submission_id}}/attachments", "post"),
        ],
    )
    def test_every_intake_endpoint_requires_a_token(self, path, method, schema):
        """Unlike `/program` and `/seasons`, all of these are about one person."""
        assert schema["paths"][path][method].get("security")


class TestASubmitterCannotGradeTheirOwnFinding:
    @pytest.mark.parametrize(
        "field", ["category", "severity", "status", "publicResponse", "adminNotes", "awardKobo"]
    )
    def test_the_request_model_has_no_triage_field(self, field, schema):
        properties = schema["components"]["schemas"]["SubmissionCreateRequest"]["properties"]
        assert field not in properties

    def test_the_tester_may_state_their_own_estimate(self, schema):
        """Captured, and never used for money. It is how we learn who calibrates well."""
        assert (
            "reportedSeverity"
            in schema["components"]["schemas"]["SubmissionCreateRequest"]["properties"]
        )

    def test_the_application_request_only_adds_consent_fields(self, schema):
        """An application is a submission plus consent, and nothing else. Extra fields here would be a
        second shape for the same thing, and the two would drift."""
        base = set(schema["components"]["schemas"]["SubmissionCreateRequest"]["properties"])
        application = set(schema["components"]["schemas"]["ApplicationCreateRequest"]["properties"])
        assert application - base == {"acceptTerms", "acceptedRulesVersion"}


class TestTheResponseKeepsStaffNotesOut:
    def test_submission_view_has_no_admin_notes(self, schema):
        assert "adminNotes" not in schema["components"]["schemas"]["SubmissionView"]["properties"]

    def test_submission_view_does_carry_the_public_response(self, schema):
        """The half a tester is meant to read. Its existence is why the private note can stay private."""
        assert "publicResponse" in schema["components"]["schemas"]["SubmissionView"]["properties"]

    def test_the_award_is_nullable(self, schema):
        """`null` means "not awarded yet", which the dashboard shows differently from an award of `0`.

        Collapsing the two would tell a tester their finding was worth nothing while it was still in the
        queue.
        """
        spec = schema["components"]["schemas"]["SubmissionView"]["properties"]["awardKobo"]
        variants = {v.get("type") for v in spec.get("anyOf", [])} or {spec.get("type")}
        assert "integer" in variants
        assert "null" in variants

    def test_the_view_names_its_season(self, schema):
        """A submission read out of context needs to say which season graded it, because the amounts
        differ between seasons."""
        properties = schema["components"]["schemas"]["SubmissionView"]["properties"]
        assert "seasonNumber" in properties
        assert "programId" in properties


class TestRequiredNarrative:
    @pytest.mark.parametrize(
        "field", ["title", "stepsToReproduce", "expectedResult", "actualResult"]
    )
    def test_the_four_fields_that_make_a_report_reproducible_are_required(self, field, schema):
        """A single free-text box collects "the app is broken", which a triager can neither grade nor pay
        for. The structure is what makes the report worth money."""
        definition = schema["components"]["schemas"]["SubmissionCreateRequest"]
        assert field in definition["required"]

    def test_optional_context_stays_optional(self, schema):
        """A web tester has no build number, and guessing is worse than a null."""
        required = set(schema["components"]["schemas"]["SubmissionCreateRequest"]["required"])
        for optional in ("appVersion", "buildNumber", "deviceModel", "osVersion", "route"):
            assert optional not in required

    def test_the_narrative_fields_are_bounded(self, schema):
        """Unbounded text on an unauthenticated-adjacent write is a storage and rendering problem, and a
        50 000-word reproduction is not a better report."""
        properties = schema["components"]["schemas"]["SubmissionCreateRequest"]["properties"]
        for field in ("title", "stepsToReproduce", "expectedResult", "actualResult"):
            assert properties[field].get("maxLength")


class TestAttachmentRules:
    def test_the_allowlist_covers_what_a_triager_can_open(self):
        assert "image/png" in attachments.ALLOWED_CONTENT_TYPES
        assert "video/mp4" in attachments.ALLOWED_CONTENT_TYPES

    @pytest.mark.parametrize(
        "rejected", ["application/pdf", "text/html", "application/zip", "video/quicktime"]
    )
    def test_everything_else_is_refused(self, rejected):
        result = attachments.validate(content_type=rejected, size=1024)
        assert result is not None
        assert result.code == "ATTACHMENT_TYPE"

    def test_a_missing_content_type_is_refused(self):
        """Fail closed. An upload we cannot classify is one a triager may not be able to open."""
        assert attachments.validate(content_type=None, size=1024) is not None

    def test_a_charset_suffix_is_tolerated(self):
        """Some clients send `image/png; charset=binary`. Refusing that would reject a valid screenshot on
        a formatting detail."""
        assert attachments.validate(content_type="image/png; charset=binary", size=1024) is None

    def test_case_is_ignored(self):
        assert attachments.validate(content_type="IMAGE/PNG", size=1024) is None

    def test_an_empty_file_is_refused(self):
        result = attachments.validate(content_type="image/png", size=0)
        assert result is not None and result.code == "ATTACHMENT_EMPTY"

    def test_an_oversized_file_is_refused_with_the_limit_in_the_message(self):
        result = attachments.validate(content_type="image/png", size=attachments.MAX_BYTES + 1)
        assert result is not None
        assert result.code == "ATTACHMENT_TOO_LARGE"
        assert "10 MB" in result.message

    def test_the_ceiling_admits_a_full_resolution_screenshot(self):
        assert attachments.validate(content_type="image/png", size=8 * 1024 * 1024) is None

    def test_the_storage_path_identifies_both_owner_and_finding(self):
        """So an orphaned object — one uploaded just before its row was refused — is identifiable by path
        alone, without consulting the database."""
        path = attachments.upload_path(user_id="user_1", submission_id="sub_1")
        assert path == "bug-hunt/user_1/sub_1"


class TestPublishedRules:
    def test_the_reapply_cooldown_is_the_documented_48_hours(self):
        assert submission_service.REAPPLY_COOLDOWN.total_seconds() == 48 * 3600

    def test_the_submission_window_is_rolling_24_hours(self):
        """Rolling rather than calendar, because a calendar day needs the tester's timezone and
        `User.timezone` defaults to UTC and was never prompted for — a calendar rule would silently give
        Lagos testers a limit that resets mid-afternoon."""
        assert submission_service.SUBMISSION_WINDOW.total_seconds() == 24 * 3600

    def test_evidence_can_only_be_added_before_a_ruling(self):
        assert submission_service.ATTACHABLE_STATUSES == frozenset({"submitted", "in_review"})
        assert "accepted" not in submission_service.ATTACHABLE_STATUSES
