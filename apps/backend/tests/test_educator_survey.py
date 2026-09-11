"""Educator research survey — the instrument's rules, and the public API that enforces them.

No database. The repository is an in-memory stand-in, because what matters here are the research
rules — consent before a row exists, partial saves persisting, contact details never entering the
answer map, stale answers pruned rather than rejected — and none of those are properties of Postgres.

The tests are organised around the four things that would invalidate a research run if they broke,
rather than around HTTP verbs.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.domains.research import instrument  # noqa: E402
from src.domains.research import routes as survey_routes  # noqa: E402
from src.domains.research.services import survey_service  # noqa: E402

CONSENT = "yes_i_agree"
DECLINE = "no_i_do_not_agree"


class FakeSurveyRepo:
    """In-memory `EducatorSurveyRepository`."""

    def __init__(self) -> None:
        self.rows: dict[str, SimpleNamespace] = {}
        self.contacts: dict[str, str] = {}
        self._seq = 0

    async def create(self, data: dict) -> SimpleNamespace:
        self._seq += 1
        row = SimpleNamespace(
            id=f"resp_{self._seq}",
            token_hash=data["token_hash"],
            answers=dict(data.get("answers") or {}),
            instrument_version=data.get("instrument_version", 1),
            status=data.get("status", "partial"),
            submitted_at=None,
            admin_status="NEW",
            last_section=data.get("last_section", 0),
            created_at=datetime.now(UTC),
            updated_at=None,
        )
        self.rows[row.id] = row
        return row

    async def get_by_token_hash(self, token_hash: str):
        for row in self.rows.values():
            if row.token_hash == token_hash:
                return row
        return None

    async def save_section(self, response_id: str, *, answers: dict, last_section: int):
        row = self.rows.get(response_id)
        if row is None or row.status != "partial":
            return None
        row.answers = dict(answers)
        row.last_section = max(row.last_section, last_section)
        return row

    async def mark_complete(self, response_id: str, *, answers: dict) -> bool:
        # Stands in for a conditional UPDATE; in production the status check and the write are one
        # statement so the database resolves a double submit. Sequentially they agree.
        row = self.rows.get(response_id)
        if row is None or row.status != "partial":
            return False
        row.answers = dict(answers)
        row.status = "complete"
        row.submitted_at = datetime.now(UTC)
        return True

    async def upsert_contact(self, response_id: str, detail: str | None) -> None:
        if detail is None or not detail.strip():
            self.contacts.pop(response_id, None)
        else:
            self.contacts[response_id] = detail.strip()

    async def get_contact(self, response_id: str):
        return self.contacts.get(response_id)


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(survey_routes.router, prefix="/api/v1/public/educator-survey")
    from src.shared.exceptions import MaigieError
    from src.shared.exceptions.handlers import maigie_error_handler

    app.add_exception_handler(MaigieError, maigie_error_handler)
    return TestClient(app, raise_server_exceptions=False)


class SurveyApiTests(unittest.TestCase):
    """Consent, resume, section saves, and submit over the public surface."""

    BASE = "/api/v1/public/educator-survey"

    def setUp(self) -> None:
        self.repo = FakeSurveyRepo()
        self._patch = patch.object(survey_service, "educator_survey_repo", self.repo)
        self._patch.start()
        self.client = _build_client()

    def tearDown(self) -> None:
        self._patch.stop()

    def _start(self) -> str:
        res = self.client.post(self.BASE, json={"consent": CONSENT})
        self.assertEqual(res.status_code, 201, res.text)
        return res.json()["token"]

    # --- consent ---

    def test_consent_starts_a_response_and_records_the_answer(self):
        token = self._start()
        row = list(self.repo.rows.values())[0]
        self.assertEqual(row.answers["Q1"], CONSENT)
        self.assertEqual(row.status, "partial")
        self.assertTrue(token)

    def test_declining_stores_nothing(self):
        res = self.client.post(self.BASE, json={"consent": DECLINE})
        self.assertEqual(res.status_code, 422, res.text)
        self.assertEqual(self.repo.rows, {}, "a respondent who declines must leave no row behind")

    def test_honeypot_stores_nothing(self):
        res = self.client.post(self.BASE, json={"consent": CONSENT, "honeypot": "http://spam"})
        self.assertEqual(res.status_code, 422)
        self.assertEqual(self.repo.rows, {})

    # --- token authorisation ---

    def test_read_requires_a_token(self):
        self._start()
        self.assertEqual(self.client.get(self.BASE).status_code, 401)

    def test_unknown_token_is_a_404(self):
        self._start()
        res = self.client.get(self.BASE, headers={"X-Survey-Token": "not-a-real-token"})
        self.assertEqual(res.status_code, 404)

    def test_resume_returns_saved_answers(self):
        token = self._start()
        self.client.patch(
            self.BASE,
            json={"section": 2, "answers": {"Q12": "weekly"}},
            headers={"X-Survey-Token": token},
        )
        res = self.client.get(self.BASE, headers={"X-Survey-Token": token})
        body = res.json()
        self.assertEqual(body["answers"]["Q12"], "weekly")
        self.assertEqual(body["lastSection"], 2)
        self.assertEqual(body["status"], "partial")

    # --- section saves ---

    def test_a_section_save_persists_without_finishing(self):
        token = self._start()
        res = self.client.patch(
            self.BASE,
            json={"section": 1, "answers": {"Q6": "26_50", "Q7": "4_7_years"}},
            headers={"X-Survey-Token": token},
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(
            res.json()["status"], "partial", "a partial response is data, not a failure"
        )

    def test_an_unknown_question_is_rejected(self):
        token = self._start()
        res = self.client.patch(
            self.BASE,
            json={"section": 1, "answers": {"Q999": "anything"}},
            headers={"X-Survey-Token": token},
        )
        self.assertEqual(res.status_code, 422)
        self.assertIn("Q999", res.text)

    def test_an_option_outside_the_instrument_is_rejected(self):
        token = self._start()
        res = self.client.patch(
            self.BASE,
            json={"section": 1, "answers": {"Q7": "since_the_dawn_of_time"}},
            headers={"X-Survey-Token": token},
        )
        self.assertEqual(res.status_code, 422)

    def test_selection_maximum_is_enforced(self):
        token = self._start()
        options = [o["value"] for o in instrument.questions_by_id()["Q13"]["options"]][:4]
        res = self.client.patch(
            self.BASE,
            json={"section": 2, "answers": {"Q13": options}},
            headers={"X-Survey-Token": token},
        )
        self.assertEqual(res.status_code, 422, "Q13 allows at most three")

    def test_null_clears_an_answer(self):
        token = self._start()
        headers = {"X-Survey-Token": token}
        self.client.patch(
            self.BASE, json={"section": 1, "answers": {"Q7": "4_7_years"}}, headers=headers
        )
        self.client.patch(self.BASE, json={"section": 1, "answers": {"Q7": None}}, headers=headers)
        res = self.client.get(self.BASE, headers=headers)
        self.assertNotIn("Q7", res.json()["answers"])

    def test_consent_cannot_be_rewritten_by_a_later_section(self):
        token = self._start()
        self.client.patch(
            self.BASE,
            json={"section": 1, "answers": {"Q1": DECLINE}},
            headers={"X-Survey-Token": token},
        )
        row = list(self.repo.rows.values())[0]
        self.assertEqual(row.answers["Q1"], CONSENT)

    def test_last_section_never_goes_backwards(self):
        token = self._start()
        headers = {"X-Survey-Token": token}
        self.client.patch(self.BASE, json={"section": 5, "answers": {}}, headers=headers)
        self.client.patch(self.BASE, json={"section": 2, "answers": {}}, headers=headers)
        # Progress is how far they reached, not where they last edited: going back to fix Q12 does not
        # un-answer sections 3 to 5.
        self.assertEqual(self.client.get(self.BASE, headers=headers).json()["lastSection"], 5)

    # --- contact separation ---

    def test_contact_detail_never_enters_the_answer_map(self):
        token = self._start()
        headers = {"X-Survey-Token": token}
        self.client.patch(
            self.BASE,
            json={"section": 11, "answers": {"Q74": "yes", "Q75": "ada@example.edu"}},
            headers=headers,
        )
        row = list(self.repo.rows.values())[0]
        self.assertIn("Q74", row.answers, "willingness is analysable and stays with the answers")
        self.assertNotIn("Q75", row.answers, "the contact detail must not be in the answer map")
        self.assertEqual(self.repo.contacts[row.id], "ada@example.edu")
        # And it is not handed back on resume either.
        self.assertNotIn("Q75", self.client.get(self.BASE, headers=headers).json()["answers"])

    def test_clearing_the_contact_removes_it(self):
        token = self._start()
        headers = {"X-Survey-Token": token}
        self.client.patch(
            self.BASE, json={"section": 11, "answers": {"Q75": "a@b.c"}}, headers=headers
        )
        self.client.patch(
            self.BASE, json={"section": 11, "answers": {"Q75": None}}, headers=headers
        )
        self.assertEqual(self.repo.contacts, {})

    # --- submit ---

    def _complete_minimum(self, headers: dict) -> None:
        self.client.patch(
            self.BASE,
            json={"section": 11, "answers": {"Q71": "Marking takes my evenings."}},
            headers=headers,
        )

    def test_submit_requires_the_required_question(self):
        token = self._start()
        headers = {"X-Survey-Token": token}
        res = self.client.post(f"{self.BASE}/submit", headers=headers)
        self.assertEqual(res.status_code, 200, "an unfinished form is a conversation, not an error")
        body = res.json()
        self.assertFalse(body["submitted"])
        self.assertEqual(body["missing"], ["Q71"])
        self.assertEqual(body["status"], "partial")

    def test_submit_completes_and_stamps(self):
        token = self._start()
        headers = {"X-Survey-Token": token}
        self._complete_minimum(headers)
        body = self.client.post(f"{self.BASE}/submit", headers=headers).json()
        self.assertTrue(body["submitted"])
        self.assertEqual(body["status"], "complete")
        row = list(self.repo.rows.values())[0]
        self.assertIsNotNone(row.submitted_at)

    def test_submit_enforces_select_exactly_three(self):
        token = self._start()
        headers = {"X-Survey-Token": token}
        self._complete_minimum(headers)
        self.client.patch(
            self.BASE,
            json={"section": 9, "answers": {"Q60": ["quiz_generation", "announcements"]}},
            headers=headers,
        )
        body = self.client.post(f"{self.BASE}/submit", headers=headers).json()
        self.assertFalse(body["submitted"])
        self.assertEqual(body["incomplete"], ["Q60"])

    def test_submit_prunes_answers_invalidated_by_a_later_edit(self):
        token = self._start()
        headers = {"X-Survey-Token": token}
        self._complete_minimum(headers)
        self.client.patch(
            self.BASE,
            json={
                "section": 4,
                "answers": {"Q26": ["zoom"], "Q27": ["other"], "Q27_other": "wifi"},
            },
            headers=headers,
        )
        # The respondent goes back and says they run no live sessions at all.
        self.client.patch(
            self.BASE,
            json={"section": 4, "answers": {"Q26": ["i_do_not_conduct_live_online_sessions"]}},
            headers=headers,
        )
        body = self.client.post(f"{self.BASE}/submit", headers=headers).json()
        self.assertTrue(body["submitted"])
        self.assertCountEqual(body["pruned"], ["Q27", "Q27_other"])
        row = list(self.repo.rows.values())[0]
        self.assertNotIn("Q27", row.answers)
        self.assertIn("Q26", row.answers)

    def test_submit_is_idempotent(self):
        token = self._start()
        headers = {"X-Survey-Token": token}
        self._complete_minimum(headers)
        first = self.client.post(f"{self.BASE}/submit", headers=headers).json()
        second = self.client.post(f"{self.BASE}/submit", headers=headers).json()
        self.assertTrue(first["submitted"])
        self.assertFalse(second["submitted"])
        self.assertEqual(second["status"], "complete")

    def test_a_submitted_response_accepts_no_more_answers(self):
        token = self._start()
        headers = {"X-Survey-Token": token}
        self._complete_minimum(headers)
        self.client.post(f"{self.BASE}/submit", headers=headers)
        res = self.client.patch(
            self.BASE, json={"section": 1, "answers": {"Q7": "4_7_years"}}, headers=headers
        )
        self.assertEqual(res.status_code, 409)

    # --- instrument metadata ---

    def test_instrument_endpoint_reports_version_and_checksum(self):
        body = self.client.get(f"{self.BASE}/instrument").json()
        self.assertEqual(body["instrumentVersion"], instrument.version())
        self.assertEqual(body["questionCount"], 75)
        self.assertEqual(len(body["checksum"]), 64)
        self.assertNotIn(
            "questions", body, "the bank itself is not served; the public site ships it"
        )


class InstrumentRuleTests(unittest.TestCase):
    """The question bank's own guarantees, independent of transport."""

    def test_every_question_is_reachable_and_typed(self):
        bank = instrument.load()
        self.assertEqual(len(bank["questions"]), 75)
        for question in bank["questions"]:
            self.assertIn("answer", question)
            self.assertTrue(question["prompt"], f'{question["id"]} has no prompt')
            if question["answer"]["type"] in {"single", "multi"}:
                self.assertTrue(
                    instrument.options_for(question), f'{question["id"]} has no options'
                )

    def test_the_concept_is_attached_to_section_8_only(self):
        # The instrument's hard rule: the concept description must not reach a respondent before they
        # have described their own workflow and pain (Q1–Q48).
        bank = instrument.load()
        revealing = [s["id"] for s in bank["sections"] if s.get("revealsConcept")]
        self.assertEqual(revealing, ["section_8"])
        self.assertTrue(bank["concept"].startswith("Imagine a platform"))

    def test_the_feature_list_is_attached_to_section_9_only(self):
        bank = instrument.load()
        revealing = [s["id"] for s in bank["sections"] if s.get("revealsFeatureList")]
        self.assertEqual(revealing, ["section_9"])

    def test_no_question_before_section_8_mentions_maigie(self):
        # A cheaper, blunter check than reading the copy: if the word appears in a prompt in the
        # behavioural sections, the concept has leaked into the part of the instrument that must stay
        # neutral.
        early = [
            q
            for q in instrument.load()["questions"]
            if q["section"] in {f"section_{n}" for n in range(1, 8)}
        ]
        self.assertEqual([q["id"] for q in early if "maigie" in q["prompt"].lower()], [])

    def test_gates_only_reference_earlier_questions(self):
        for question in instrument.load()["questions"]:
            rule = question.get("displayIf")
            if not rule:
                continue
            self.assertLess(
                int(rule["question"][1:]),
                int(question["id"][1:]),
                f'{question["id"]} is gated on a later question',
            )

    def test_gate_values_exist_in_the_referenced_options(self):
        for question in instrument.load()["questions"]:
            rule = question.get("displayIf")
            if not rule:
                continue
            allowed = {
                o["value"] for o in instrument.questions_by_id()[rule["question"]]["options"]
            }
            for value in rule["notAnyOf"]:
                self.assertIn(
                    value, allowed, f'{question["id"]} gates on a value that cannot occur'
                )

    def test_an_unanswered_gate_leaves_the_question_visible(self):
        # Silence is not a disqualification: someone who skipped Q26 has not said they run no live
        # sessions, and hiding the follow-ups would drop them from a section they may belong in.
        self.assertTrue(instrument.is_visible(instrument.questions_by_id()["Q27"], {}))

    def test_peer_matching_opinions_are_asked_of_everyone(self):
        # Q39–Q41 ask about current peer-group practice and are gated. Q42–Q44 ask what a respondent
        # would accept from AI-assisted matching, which an educator who runs no peer groups can answer
        # — and whose objections are worth more than most. Gating those would discard them.
        no_peer_learning = {"Q38": "no_and_i_do_not_see_a_current_need"}
        for gated in ("Q39", "Q40", "Q41"):
            self.assertFalse(
                instrument.is_visible(instrument.questions_by_id()[gated], no_peer_learning), gated
            )
        for ungated in ("Q42", "Q43", "Q44"):
            self.assertTrue(
                instrument.is_visible(instrument.questions_by_id()[ungated], no_peer_learning),
                ungated,
            )

    def test_pricing_questions_hide_only_when_nobody_would_pay(self):
        self.assertFalse(
            instrument.is_visible(
                instrument.questions_by_id()["Q68"], {"Q65": "nobody_would_pay_in_my_context"}
            )
        )
        # "I do not know" still sees them: Q68 has its own "I cannot estimate" option, and the
        # instrument's notes keep those respondents in the adoption analysis.
        self.assertTrue(
            instrument.is_visible(instrument.questions_by_id()["Q68"], {"Q65": "i_do_not_know"})
        )

    def test_contact_question_is_never_required(self):
        self.assertFalse(instrument.questions_by_id()["Q75"].get("required", False))
        self.assertNotIn("Q75", instrument.missing_required({"Q74": "yes"}))

    def test_only_consent_and_the_open_pain_question_are_required(self):
        required = [q["id"] for q in instrument.load()["questions"] if q.get("required")]
        self.assertEqual(required, ["Q1", "Q71"])


class ScaleOptOutTests(unittest.TestCase):
    """Two scales carry a non-numeric escape, and it has to be answerable.

    Q41 ends with "Not applicable" and Q66 with "Not applicable because learners would not pay". Typed
    as a bare 1–5 integer, both escapes were unanswerable — and that is not cosmetic: for an educator
    whose learners would never pay, "not applicable" is the honest answer, and refusing it pushes them
    into inventing a comfort score. These tests pin the fix and the shape of the bug.
    """

    def test_every_scale_with_a_non_numeric_option_declares_it(self):
        # The general guard. A future revision that adds another escape to another scale is caught here
        # rather than by a respondent who cannot answer.
        for q in instrument.load()["questions"]:
            if q["answer"]["type"] != "scale":
                continue
            escapes = [o for o in q["options"] if not o["label"][:1].isdigit()]
            if escapes:
                self.assertEqual(
                    q["answer"].get("naOption"),
                    escapes[0]["value"],
                    f'{q["id"]} has an escape option the answer shape does not admit',
                )
            else:
                self.assertNotIn("naOption", q["answer"])

    def test_the_two_known_escapes_are_accepted(self):
        self.assertEqual(instrument.validate_answer("Q41", "not_applicable"), "not_applicable")
        self.assertEqual(
            instrument.validate_answer("Q66", "not_applicable_because_learners_would_not_pay"),
            "not_applicable_because_learners_would_not_pay",
        )

    def test_a_number_is_still_accepted(self):
        self.assertEqual(instrument.validate_answer("Q66", 3), 3)

    def test_another_questions_escape_is_not_accepted(self):
        # Q41's slug on Q66 is not "close enough" — it would record an answer the respondent never
        # gave, on a question with a differently worded opt-out.
        with self.assertRaises(instrument.AnswerError):
            instrument.validate_answer("Q66", "not_applicable")

    def test_a_scale_without_an_escape_still_refuses_text(self):
        with self.assertRaises(instrument.AnswerError):
            instrument.validate_answer("Q49", "not_applicable")


class AnswerRenderingTests(unittest.TestCase):
    """`describe_answers` turns stored slugs into the instrument's own wording, for review."""

    def test_scale_answers_carry_their_wording(self):
        described = instrument.describe_answers({"Q49": 4})
        item = described[0]["items"][0]
        # Scale slugs derive from labels (`4_very_relevant`), not from the number, so this is the case
        # a naive lookup by `str(value)` gets wrong — it would render a bare "4".
        self.assertEqual(item["values"], ["4 — Very relevant"])
        self.assertEqual(item["questionId"], "Q49")

    def test_scale_opt_out_renders_as_its_label(self):
        described = instrument.describe_answers(
            {"Q66": "not_applicable_because_learners_would_not_pay"}
        )
        self.assertEqual(
            described[0]["items"][0]["values"],
            ["Not applicable because learners would not pay"],
        )

    def test_choice_answers_render_labels_and_other_text(self):
        described = instrument.describe_answers({"Q13": ["other"], "Q13_other": "archiving"})
        item = described[0]["items"][0]
        self.assertEqual(item["values"], ["Other"])
        self.assertEqual(item["otherText"], "archiving")

    def test_free_text_is_returned_whole(self):
        described = instrument.describe_answers({"Q71": "Marking takes my evenings."})
        item = described[0]["items"][0]
        self.assertEqual(item["text"], "Marking takes my evenings.")
        self.assertEqual(item["values"], [])

    def test_unanswered_questions_are_omitted(self):
        # A reviewer scanning a partial response wants the answers, not fifty-five blank rows.
        described = instrument.describe_answers({"Q71": "x"})
        self.assertEqual(sum(len(s["items"]) for s in described), 1)
        self.assertEqual([s["number"] for s in described], [11])

    def test_sections_come_back_in_instrument_order(self):
        described = instrument.describe_answers({"Q71": "x", "Q6": "26_50", "Q49": 3})
        self.assertEqual([s["number"] for s in described], [1, 8, 11])

    def test_borrowed_option_lists_resolve(self):
        # Q61 has no options of its own; it reuses Q60's twenty capabilities.
        described = instrument.describe_answers({"Q61": "quiz_generation"})
        self.assertEqual(described[0]["items"][0]["values"], ["Quiz generation"])
