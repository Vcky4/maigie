"""Landing drafts — the public draft API and the signup claim.

No database. The repository is replaced with an in-memory stand-in, because what these tests are
about is the *rules* — token authorisation, expiry, single-use claiming, the generation cap, and the
fail-closed rate limit — and none of those are properties of Postgres. The one place that reasoning
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
        if row is None:
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
    """The public surface: create, read, update, generate."""

    def setUp(self) -> None:
        self.repo = FakeDraftRepo()
        self._patches = [
            patch.object(draft_service, "landing_draft_repo", self.repo),
            # Generation is rate-limited with the strict limiter, which refuses when the cache is
            # unavailable — and in tests it always is. Allowing it here keeps these cases about the
            # endpoint; the refusal itself is asserted in FailClosedTests below.
            patch.object(
                draft_routes, "check_rate_limit_strict", self._allow_rate_limit
            ),
            patch.object(draft_service, "_generate_items", self._fake_generate),
        ]
        for p in self._patches:
            p.start()
        self.client = _build_client()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()

    @staticmethod
    async def _allow_rate_limit(_key, _max, _window):
        return True, 5

    @staticmethod
    async def _fake_generate(draft):
        from src.domains.landing_drafts.models import DraftPreviewItem

        return [DraftPreviewItem(label="A study plan", detail=f"For {draft.purpose}.")]

    # --- create ---

    def test_create_returns_token_once(self):
        res = self.client.post("/api/v1/public/landing-drafts", json={"purpose": "exam_prep"})
        self.assertEqual(res.status_code, 201, res.text)
        body = res.json()
        self.assertTrue(body["token"])
        self.assertEqual(body["purpose"], "exam_prep")
        self.assertEqual(body["status"], "open")
        self.assertTrue(body["canGenerate"])

        # The token is never served again.
        read = self.client.get(
            f"/api/v1/public/landing-drafts/{body['id']}",
            headers={"X-Draft-Token": body["token"]},
        )
        self.assertEqual(read.status_code, 200, read.text)
        self.assertNotIn("token", read.json())

    def test_create_rejects_unknown_purpose(self):
        res = self.client.post("/api/v1/public/landing-drafts", json={"purpose": "world_domination"})
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
            json={"subjects": ["Biology"], "examName": "A-Level Biology", "examDate": "2027-06-01"},
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

    def test_update_rejects_oversized_goals(self):
        created = self.client.post("/api/v1/public/landing-drafts", json={}).json()
        res = self.client.patch(
            f"/api/v1/public/landing-drafts/{created['id']}",
            headers={"X-Draft-Token": created["token"]},
            json={"goals": "x" * 5000},
        )
        self.assertEqual(res.status_code, 422)

    def test_editing_clears_a_stale_preview(self):
        created = self.client.post(
            "/api/v1/public/landing-drafts", json={"purpose": "exam_prep"}
        ).json()
        headers = {"X-Draft-Token": created["token"]}
        self.client.patch(
            f"/api/v1/public/landing-drafts/{created['id']}",
            headers=headers,
            json={"subjects": ["Biology"]},
        )
        gen = self.client.post(
            f"/api/v1/public/landing-drafts/{created['id']}/generate", headers=headers
        )
        self.assertTrue(gen.json()["preview"])

        res = self.client.patch(
            f"/api/v1/public/landing-drafts/{created['id']}",
            headers=headers,
            json={"subjects": ["Chemistry"]},
        )
        self.assertEqual(res.json()["preview"], [])

    # --- generate ---

    def test_generate_is_capped_per_draft(self):
        created = self.client.post(
            "/api/v1/public/landing-drafts", json={"purpose": "exam_prep"}
        ).json()
        headers = {"X-Draft-Token": created["token"]}
        url = f"/api/v1/public/landing-drafts/{created['id']}/generate"

        for _ in range(draft_service.MAX_GENERATES_PER_DRAFT):
            self.assertEqual(self.client.post(url, headers=headers).status_code, 200)

        blocked = self.client.post(url, headers=headers)
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["code"], "DRAFT_GENERATE_LIMIT")

    def test_can_generate_flag_reflects_the_cap(self):
        created = self.client.post("/api/v1/public/landing-drafts", json={}).json()
        headers = {"X-Draft-Token": created["token"]}
        url = f"/api/v1/public/landing-drafts/{created['id']}/generate"
        for _ in range(draft_service.MAX_GENERATES_PER_DRAFT):
            last = self.client.post(url, headers=headers)
        self.assertFalse(last.json()["canGenerate"])


class FailClosedTests(unittest.TestCase):
    """The generate limiter must refuse rather than degrade open."""

    def setUp(self) -> None:
        self.repo = FakeDraftRepo()
        self._patch = patch.object(draft_service, "landing_draft_repo", self.repo)
        self._patch.start()
        self.client = _build_client()

    def tearDown(self) -> None:
        self._patch.stop()

    def test_generate_refuses_when_cache_is_unavailable(self):
        """The whole reason `check_rate_limit_strict` exists.

        No cache is connected in tests, so the shared limiter would wave this through — which on an
        unauthenticated endpoint that spends LLM budget is an open tap. This asserts the opposite.
        """
        created = self.client.post("/api/v1/public/landing-drafts", json={}).json()
        res = self.client.post(
            f"/api/v1/public/landing-drafts/{created['id']}/generate",
            headers={"X-Draft-Token": created["token"]},
        )
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.json()["detail"]["code"], "GENERATE_RATE_LIMITED")
        self.assertEqual(res.headers.get("Retry-After"), str(draft_routes.GENERATE_LIMIT[1]))

    def test_create_still_works_when_cache_is_unavailable(self):
        """Creating a draft costs nothing, so it keeps the fail-open limiter."""
        res = self.client.post("/api/v1/public/landing-drafts", json={"purpose": "exam_prep"})
        self.assertEqual(res.status_code, 201)


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

    async def test_exam_draft_runs_the_exam_onboarding_sequence(self):
        _draft, token = await self._open_draft(
            purpose="exam_prep",
            subjects=["Biology"],
            exam_name="A-Level Biology",
            exam_date=date(2027, 6, 1),
            goals_text="Aim for an A",
        )
        result = await draft_service.claim_draft(token=token, user_id="user_1")

        self.assertTrue(result["applied"])
        self.assertEqual([name for name, _ in self.calls], ["set_purpose", "set_exam_details"])
        details = self.calls[1][1]
        self.assertEqual(details["exam_name"], "A-Level Biology")
        self.assertEqual(details["subjects"], ["Biology"])
        self.assertEqual(details["goals"], "Aim for an A")

    async def test_skill_draft_runs_the_skill_sequence(self):
        _draft, token = await self._open_draft(purpose="skill_building", subjects=["Python"])
        await draft_service.claim_draft(token=token, user_id="user_1")
        self.assertEqual([name for name, _ in self.calls], ["set_purpose", "set_skill_details"])

    async def test_generic_draft_falls_back_to_subjects(self):
        _draft, token = await self._open_draft(purpose="general_learning", subjects=["Anatomy"])
        await draft_service.claim_draft(token=token, user_id="user_1")
        self.assertEqual([name for name, _ in self.calls], ["set_purpose", "set_subjects"])

    async def test_exam_name_becomes_the_subject_when_none_was_given(self):
        """Auto-setup refuses a profile with no subjects, so a name-only draft must supply one."""
        _draft, token = await self._open_draft(purpose="exam_prep", exam_name="MCAT")
        await draft_service.claim_draft(token=token, user_id="user_1")
        self.assertEqual(self.calls[1][1]["subjects"], ["MCAT"])

    async def test_purpose_only_draft_applies_without_content(self):
        _draft, token = await self._open_draft(purpose="exam_prep")
        result = await draft_service.claim_draft(token=token, user_id="user_1")
        self.assertTrue(result["applied"])
        self.assertEqual([name for name, _ in self.calls], ["set_purpose"])

    async def test_claim_is_single_use(self):
        _draft, token = await self._open_draft(purpose="exam_prep", subjects=["Biology"])
        first = await draft_service.claim_draft(token=token, user_id="user_1")
        second = await draft_service.claim_draft(token=token, user_id="user_2")

        self.assertTrue(first["applied"])
        self.assertFalse(second["applied"])
        self.assertEqual(second["reason"], "already_claimed")
        # And the second attempt ran no onboarding at all.
        self.assertEqual([name for name, _ in self.calls], ["set_purpose", "set_exam_details"])

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


class PreviewCoercionTests(unittest.TestCase):
    """Whatever the model returns, the visitor gets three usable lines or the static sketch."""

    def setUp(self) -> None:
        self.fallback = draft_service._static_preview(
            SimpleNamespace(purpose="exam_prep", exam_date=None), "Biology"
        )

    def test_well_formed_array(self):
        items = draft_service._coerce_items(
            [{"label": "A plan", "detail": "Milestones."}], self.fallback
        )
        self.assertEqual(items[0].label, "A plan")

    def test_wrapped_in_an_object(self):
        items = draft_service._coerce_items(
            {"items": [{"label": "A plan", "detail": "Milestones."}]}, self.fallback
        )
        self.assertEqual(items[0].label, "A plan")

    def test_json_in_a_string(self):
        items = draft_service._coerce_items(
            '[{"label": "A plan", "detail": "Milestones."}]', self.fallback
        )
        self.assertEqual(items[0].label, "A plan")

    def test_too_many_items_are_trimmed(self):
        raw = [{"label": f"L{i}", "detail": "D"} for i in range(9)]
        self.assertEqual(len(draft_service._coerce_items(raw, self.fallback)), 3)

    def test_incomplete_entries_are_dropped(self):
        items = draft_service._coerce_items(
            [{"label": "Only a label"}, {"label": "Good", "detail": "Fine."}], self.fallback
        )
        self.assertEqual([i.label for i in items], ["Good"])

    def test_unusable_reply_falls_back(self):
        for raw in (None, 42, "not json", [], [{"nope": 1}]):
            items = draft_service._coerce_items(raw, self.fallback)
            self.assertEqual(items, self.fallback, raw)

    def test_long_strings_are_truncated(self):
        items = draft_service._coerce_items(
            [{"label": "x" * 500, "detail": "y" * 900}], self.fallback
        )
        self.assertLessEqual(len(items[0].label), 80)
        self.assertLessEqual(len(items[0].detail), 280)


class PromptTests(unittest.TestCase):
    """The prompt is the only place a visitor's free text reaches a model."""

    def test_goals_text_is_truncated_into_the_prompt(self):
        draft = SimpleNamespace(
            purpose="exam_prep",
            exam_name="MCAT",
            exam_date=date(2027, 6, 1),
            goals_text="z" * 2000,
            subjects=["Biology"],
        )
        prompt = draft_service._preview_prompt(draft, ["Biology"], "Biology")
        self.assertIn("MCAT", prompt)
        self.assertIn("2027-06-01", prompt)
        self.assertLess(prompt.count("z"), 400)

    def test_prompt_forbids_outcome_promises(self):
        draft = SimpleNamespace(
            purpose="exam_prep", exam_name=None, exam_date=None, goals_text=None, subjects=[]
        )
        prompt = draft_service._preview_prompt(draft, [], "Biology")
        self.assertIn("Do not promise outcomes", prompt)


if __name__ == "__main__":
    unittest.main()
