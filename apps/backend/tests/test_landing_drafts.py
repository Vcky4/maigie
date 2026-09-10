"""Landing drafts — the public draft API and the signup claim.

No database. The repository is replaced with an in-memory stand-in, because what these tests are
about is the *rules* — token authorisation, expiry, deterministic persistence, and single-use
claiming — and none of those are properties of Postgres. The one place that reasoning
would be wrong is `mark_claimed`, whose whole point is that the database picks a winner between two
concurrent claims; the fake models that by doing the status check and the write together, and the
comment there says so.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import os
import unittest
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.domains.landing_drafts import routes as draft_routes  # noqa: E402
from src.domains.landing_drafts.services import draft_service  # noqa: E402
from src.shared.auth import get_current_user  # noqa: E402

USER = SimpleNamespace(id="user_1", name="Ada", tier="FREE", is_onboarded=False)


class FakeDraftRepo:
    """In-memory `LandingDraftRepository`."""

    def __init__(self) -> None:
        self.rows: dict[str, SimpleNamespace] = {}
        self._seq = 0

    async def create(self, data: dict) -> SimpleNamespace:
        self._seq += 1
        row = SimpleNamespace(
            id=f"draft_{self._seq}",
            token_hash=data["token_hash"],
            email=None,
            purpose=data.get("purpose"),
            subjects=None,
            goals_text=None,
            exam_name=None,
            exam_date=None,
            preview=None,
            generate_count=0,
            status=data.get("status", "open"),
            expires_at=data["expires_at"],
            claimed_at=None,
            claimed_by=None,
            schema_version=1,
        )
        self.rows[row.id] = row
        return row

    async def get_by_token_hash(self, token_hash: str):
        for row in self.rows.values():
            if row.token_hash == token_hash:
                return row
        return None

    async def get_by_id_internal(self, draft_id: str):
        return self.rows.get(draft_id)

    async def update_fields(self, draft_id: str, values: dict):
        row = self.rows.get(draft_id)
        if row is None or row.status != "open" or row.expires_at <= datetime.now(UTC):
            return None
        for key, value in values.items():
            setattr(row, key, value)
        return row

    async def mark_claimed(self, draft_id: str, user_id: str) -> bool:
        # Stands in for a conditional UPDATE. In production the status check and the write are one
        # statement so the database resolves a race; here they are adjacent under the test's single
        # thread, which gives the same answer for the sequential case these tests exercise. A real
        # concurrency test would need two connections and a live database.
        row = self.rows.get(draft_id)
        if row is None or row.status != "open" or row.expires_at <= datetime.now(UTC):
            return False
        row.status = "claimed"
        row.claimed_by = user_id
        row.claimed_at = datetime.now(UTC)
        return True


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(draft_routes.router, prefix="/api/v1/public/landing-drafts")
    from src.shared.exceptions import MaigieError
    from src.shared.exceptions.handlers import maigie_error_handler

    app.add_exception_handler(MaigieError, maigie_error_handler)
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app, raise_server_exceptions=False)


class LandingDraftApiTests(unittest.TestCase):
    """The public surface: create, token-authenticated read, and update."""

    def setUp(self) -> None:
        self.repo = FakeDraftRepo()
        self._patches = [patch.object(draft_service, "landing_draft_repo", self.repo)]
        for p in self._patches:
            p.start()
        self.client = _build_client()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()

    # --- create ---

    def test_create_returns_token_once(self):
        res = self.client.post("/api/v1/public/landing-drafts", json={"purpose": "exam_prep"})
        self.assertEqual(res.status_code, 201, res.text)
        body = res.json()
        self.assertTrue(body["token"])
        self.assertEqual(body["purpose"], "exam_prep")
        self.assertEqual(body["status"], "open")

        # The token is never served again.
        read = self.client.get(
            f"/api/v1/public/landing-drafts/{body['id']}",
            headers={"X-Draft-Token": body["token"]},
        )
        self.assertEqual(read.status_code, 200, read.text)
        self.assertNotIn("token", read.json())

    def test_create_rejects_unknown_purpose(self):
        res = self.client.post(
            "/api/v1/public/landing-drafts", json={"purpose": "world_domination"}
        )
        self.assertEqual(res.status_code, 422)

    def test_token_hash_is_stored_not_the_token(self):
        res = self.client.post("/api/v1/public/landing-drafts", json={})
        token = res.json()["token"]
        stored = [row.token_hash for row in self.repo.rows.values()]
        self.assertNotIn(token, stored)
        self.assertIn(draft_service.hash_token(token), stored)

    # --- authorisation ---

    def test_read_requires_token(self):
        created = self.client.post("/api/v1/public/landing-drafts", json={}).json()
        res = self.client.get(f"/api/v1/public/landing-drafts/{created['id']}")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()["detail"]["code"], "DRAFT_TOKEN_REQUIRED")

    def test_handoff_read_needs_only_the_opaque_token(self):
        created = self.client.post(
            "/api/v1/public/landing-drafts", json={"purpose": "exam_prep"}
        ).json()
        res = self.client.get(
            "/api/v1/public/landing-drafts/handoff",
            headers={"X-Draft-Token": created["token"]},
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["id"], created["id"])
        self.assertNotIn("token", res.json())

    def test_unknown_token_is_not_found(self):
        created = self.client.post("/api/v1/public/landing-drafts", json={}).json()
        res = self.client.get(
            f"/api/v1/public/landing-drafts/{created['id']}",
            headers={"X-Draft-Token": "not-a-real-token"},
        )
        self.assertEqual(res.status_code, 404)

    def test_right_token_wrong_id_is_not_found(self):
        """An id alone must never be enough, and a mismatch must not confirm the id exists."""
        created = self.client.post("/api/v1/public/landing-drafts", json={}).json()
        res = self.client.get(
            "/api/v1/public/landing-drafts/draft_999",
            headers={"X-Draft-Token": created["token"]},
        )
        self.assertEqual(res.status_code, 404)

    def test_expired_draft_reads_as_not_found(self):
        created = self.client.post("/api/v1/public/landing-drafts", json={}).json()
        self.repo.rows[created["id"]].expires_at = datetime.now(UTC) - timedelta(seconds=1)
        res = self.client.get(
            f"/api/v1/public/landing-drafts/{created['id']}",
            headers={"X-Draft-Token": created["token"]},
        )
        self.assertEqual(res.status_code, 404)

    # --- update ---

    def test_update_applies_only_sent_fields(self):
        created = self.client.post(
            "/api/v1/public/landing-drafts", json={"purpose": "exam_prep"}
        ).json()
        headers = {"X-Draft-Token": created["token"]}

        res = self.client.patch(
            f"/api/v1/public/landing-drafts/{created['id']}",
            headers=headers,
            json={
                "subjects": ["Biology"],
                "examName": "A-Level Biology",
                "examDate": "2027-06-01",
            },
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["subjects"], ["Biology"])
        self.assertEqual(res.json()["examName"], "A-Level Biology")

        # Omitting examName leaves it alone rather than clearing it.
        res = self.client.patch(
            f"/api/v1/public/landing-drafts/{created['id']}",
            headers=headers,
            json={"goals": "Aim for an A"},
        )
        self.assertEqual(res.json()["examName"], "A-Level Biology")
        self.assertEqual(res.json()["goalsText"], "Aim for an A")

    def test_update_stores_a_normalised_email(self):
        created = self.client.post("/api/v1/public/landing-drafts", json={}).json()
        res = self.client.patch(
            f"/api/v1/public/landing-drafts/{created['id']}",
            headers={"X-Draft-Token": created["token"]},
            json={"email": "  Ada@School.EDU "},
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["email"], "ada@school.edu")

    def test_update_rejects_a_malformed_email(self):
        created = self.client.post("/api/v1/public/landing-drafts", json={}).json()
        res = self.client.patch(
            f"/api/v1/public/landing-drafts/{created['id']}",
            headers={"X-Draft-Token": created["token"]},
            json={"email": "not-an-address"},
        )
        self.assertEqual(res.status_code, 422)

    def test_update_deduplicates_and_caps_subjects(self):
        created = self.client.post("/api/v1/public/landing-drafts", json={}).json()
        res = self.client.patch(
            f"/api/v1/public/landing-drafts/{created['id']}",
            headers={"X-Draft-Token": created["token"]},
            json={"subjects": ["Maths", "maths ", "  ", "Physics", "A", "B", "C", "D"]},
        )
        subjects = res.json()["subjects"]
        self.assertEqual(subjects[:2], ["Maths", "Physics"])
        self.assertLessEqual(len(subjects), draft_service.MAX_SUBJECTS)

    def test_update_fails_if_claim_wins_after_token_resolution(self):
        created = self.client.post("/api/v1/public/landing-drafts", json={}).json()
        original_update = self.repo.update_fields

        async def claimed_before_write(draft_id, values):
            self.repo.rows[draft_id].status = "claimed"
            return await original_update(draft_id, values)

        with patch.object(self.repo, "update_fields", claimed_before_write):
            res = self.client.patch(
                f"/api/v1/public/landing-drafts/{created['id']}",
                headers={"X-Draft-Token": created["token"]},
                json={"subjects": ["Biology"]},
            )
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["code"], "DRAFT_NOT_OPEN")

    def test_update_rejects_oversized_goals(self):
        created = self.client.post("/api/v1/public/landing-drafts", json={}).json()
        res = self.client.patch(
            f"/api/v1/public/landing-drafts/{created['id']}",
            headers={"X-Draft-Token": created["token"]},
            json={"goals": "x" * 5000},
        )
        self.assertEqual(res.status_code, 422)


class StrictLimiterUnitTests(unittest.IsolatedAsyncioTestCase):
    """`check_rate_limit_strict` itself, against the cache states it has to handle."""

    async def test_refuses_when_disconnected(self):
        from src.shared.infrastructure import rate_limit

        with patch.object(rate_limit.cache, "_connected", False):
            allowed, remaining = await rate_limit.check_rate_limit_strict("k", 5, 60)
        self.assertFalse(allowed)
        self.assertEqual(remaining, 0)

    async def test_refuses_when_increment_returns_nothing(self):
        from src.shared.infrastructure import rate_limit

        async def increment(_key, _by):
            return None

        with (
            patch.object(rate_limit.cache, "_connected", True),
            patch.object(rate_limit.cache, "redis", object()),
            patch.object(rate_limit.cache, "increment", increment),
        ):
            allowed, _ = await rate_limit.check_rate_limit_strict("k", 5, 60)
        self.assertFalse(allowed)

    async def test_refuses_when_the_cache_raises(self):
        from src.shared.infrastructure import rate_limit

        async def increment(_key, _by):
            raise RuntimeError("redis exploded")

        with (
            patch.object(rate_limit.cache, "_connected", True),
            patch.object(rate_limit.cache, "redis", object()),
            patch.object(rate_limit.cache, "increment", increment),
        ):
            allowed, _ = await rate_limit.check_rate_limit_strict("k", 5, 60)
        self.assertFalse(allowed)

    async def test_counts_and_then_refuses(self):
        from src.shared.infrastructure import rate_limit

        state = {"n": 0}

        async def increment(_key, _by):
            state["n"] += 1
            return state["n"]

        async def expire(_key, _ttl):
            return True

        with (
            patch.object(rate_limit.cache, "_connected", True),
            patch.object(rate_limit.cache, "redis", object()),
            patch.object(rate_limit.cache, "increment", increment),
            patch.object(rate_limit.cache, "expire", expire),
        ):
            first = await rate_limit.check_rate_limit_strict("k", 2, 60)
            second = await rate_limit.check_rate_limit_strict("k", 2, 60)
            third = await rate_limit.check_rate_limit_strict("k", 2, 60)

        self.assertTrue(first[0])
        self.assertTrue(second[0])
        self.assertFalse(third[0])

    async def test_shared_limiter_still_degrades_open(self):
        """The existing behaviour must not have changed for authenticated callers."""
        from src.shared.infrastructure import rate_limit

        with patch.object(rate_limit.cache, "_connected", False):
            allowed, remaining = await rate_limit.check_rate_limit("k", 5, 60)
        self.assertTrue(allowed)
        self.assertEqual(remaining, 5)


class ClaimTests(unittest.IsolatedAsyncioTestCase):
    """The signup claim: single-use, soft-failing, and reusing the onboarding sequence."""

    def setUp(self) -> None:
        self.repo = FakeDraftRepo()
        self.calls: list[tuple[str, dict]] = []
        self.profile = None

        async def get_profile_by_user(_user_id):
            return self.profile

        async def set_purpose(*, user_id, purpose):
            self.calls.append(("set_purpose", {"user_id": user_id, "purpose": purpose}))
            return SimpleNamespace(purpose=purpose)

        async def set_exam_details(**kwargs):
            self.calls.append(("set_exam_details", kwargs))
            return SimpleNamespace()

        async def set_skill_details(**kwargs):
            self.calls.append(("set_skill_details", kwargs))
            return SimpleNamespace()

        async def set_subjects(**kwargs):
            self.calls.append(("set_subjects", kwargs))
            return SimpleNamespace()

        async def complete_onboarding(*, user_id):
            self.calls.append(("complete_onboarding", {"user_id": user_id}))

        self.onboarding = SimpleNamespace(
            set_purpose=set_purpose,
            set_exam_details=set_exam_details,
            set_skill_details=set_skill_details,
            set_subjects=set_subjects,
        )
        self.personal_repo = SimpleNamespace(get_profile_by_user=get_profile_by_user)

        self._patches = [
            patch.object(draft_service, "landing_draft_repo", self.repo),
            patch(
                "src.domains.personal_learning.services.onboarding_service.set_purpose",
                set_purpose,
            ),
            patch(
                "src.domains.personal_learning.services.onboarding_service.set_exam_details",
                set_exam_details,
            ),
            patch(
                "src.domains.personal_learning.services.onboarding_service.set_skill_details",
                set_skill_details,
            ),
            patch(
                "src.domains.personal_learning.services.onboarding_service.set_subjects",
                set_subjects,
            ),
            patch(
                "src.domains.personal_learning.services.onboarding_service.complete_onboarding",
                complete_onboarding,
            ),
            patch(
                "src.domains.personal_learning.repository.personal_learning_repo."
                "get_profile_by_user",
                get_profile_by_user,
            ),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()

    async def _open_draft(self, **fields):
        draft, token = await draft_service.create_draft(purpose=fields.pop("purpose", None))
        for key, value in fields.items():
            setattr(draft, key, value)
        return draft, token

    async def test_exam_draft_returns_answers_without_running_onboarding(self):
        _draft, token = await self._open_draft(
            purpose="exam_prep",
            subjects=["Biology"],
            exam_name="A-Level Biology",
            exam_date=date(2027, 6, 1),
            goals_text="Aim for an A",
        )
        result = await draft_service.claim_draft(token=token, user_id="user_1")

        self.assertTrue(result["applied"])
        self.assertEqual(result["purpose"], "exam_prep")
        self.assertEqual(result["subjects"], ["Biology"])
        self.assertEqual(result["exam_name"], "A-Level Biology")
        self.assertEqual(result["exam_date"], date(2027, 6, 1))
        self.assertEqual(result["goals_text"], "Aim for an A")
        self.assertEqual(self.calls, [])

    async def test_skill_draft_returns_answers_without_running_onboarding(self):
        _draft, token = await self._open_draft(purpose="skill_building", subjects=["Python"])
        result = await draft_service.claim_draft(token=token, user_id="user_1")
        self.assertTrue(result["applied"])
        self.assertEqual(result["subjects"], ["Python"])
        self.assertEqual(self.calls, [])

    async def test_generic_draft_returns_answers_without_running_onboarding(self):
        _draft, token = await self._open_draft(purpose="general_learning", subjects=["Anatomy"])
        result = await draft_service.claim_draft(token=token, user_id="user_1")
        self.assertTrue(result["applied"])
        self.assertEqual(result["subjects"], ["Anatomy"])
        self.assertEqual(self.calls, [])

    async def test_exam_name_becomes_the_subject_when_none_was_given(self):
        """A name-only exam still returns the subject required by normal onboarding."""
        _draft, token = await self._open_draft(purpose="exam_prep", exam_name="MCAT")
        result = await draft_service.claim_draft(token=token, user_id="user_1")
        self.assertEqual(result["subjects"], ["MCAT"])

    async def test_purpose_only_draft_returns_without_applying_profile_fields(self):
        _draft, token = await self._open_draft(purpose="exam_prep")
        result = await draft_service.claim_draft(token=token, user_id="user_1")
        self.assertTrue(result["applied"])
        self.assertEqual(result["subjects"], [])
        self.assertEqual(self.calls, [])

    async def test_claim_is_single_use(self):
        _draft, token = await self._open_draft(purpose="exam_prep", subjects=["Biology"])
        first = await draft_service.claim_draft(token=token, user_id="user_1")
        second = await draft_service.claim_draft(token=token, user_id="user_2")

        self.assertTrue(first["applied"])
        self.assertFalse(second["applied"])
        self.assertEqual(second["reason"], "already_claimed")
        # Claim only establishes ownership; neither caller runs onboarding here.
        self.assertEqual(self.calls, [])

    async def test_same_user_can_resume_and_receive_the_same_answers(self):
        _draft, token = await self._open_draft(purpose="exam_prep", subjects=["Biology"])
        first = await draft_service.claim_draft(token=token, user_id="user_1")
        resumed = await draft_service.claim_draft(token=token, user_id="user_1")

        self.assertTrue(first["applied"])
        self.assertEqual(resumed, first)
        self.assertEqual(self.calls, [])

    async def test_unknown_token_is_soft(self):
        result = await draft_service.claim_draft(token="nonsense", user_id="user_1")
        self.assertEqual(result, {"applied": False, "reason": "not_found"})

    async def test_expired_draft_is_soft(self):
        draft, token = await self._open_draft(purpose="exam_prep", subjects=["Biology"])
        draft.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        result = await draft_service.claim_draft(token=token, user_id="user_1")
        self.assertEqual(result["reason"], "expired")
        self.assertEqual(self.calls, [])

    async def test_empty_draft_is_soft(self):
        _draft, token = await self._open_draft()
        result = await draft_service.claim_draft(token=token, user_id="user_1")
        self.assertEqual(result["reason"], "empty")

    async def test_claim_never_runs_onboarding_completion(self):
        _draft, token = await self._open_draft(purpose="exam_prep", subjects=["Biology"])
        result = await draft_service.claim_draft(token=token, user_id="user_1")

        self.assertTrue(result["applied"])
        self.assertEqual(self.calls, [])

    async def test_existing_profile_is_left_alone(self):
        """A learner who has already onboarded must not have their state rewound."""
        self.profile = SimpleNamespace(purpose="skill_building")
        _draft, token = await self._open_draft(purpose="exam_prep", subjects=["Biology"])
        result = await draft_service.claim_draft(token=token, user_id="user_1")

        self.assertFalse(result["applied"])
        self.assertEqual(result["reason"], "profile_exists")
        self.assertEqual(self.calls, [])
        # The draft is still open, so nothing was burned by the refusal.
        self.assertEqual(list(self.repo.rows.values())[0].status, "open")


if __name__ == "__main__":
    unittest.main()
