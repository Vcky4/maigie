"""Tests for the Prepare fields added so the backend covers what the UI shows.

Six things were previously rendered from a fixture because nothing served them:
per-preparation progress on the detail read, a target to measure readiness
against, topic grouping and question counts, a next-action recommendation,
question provenance inside a session, and a way to upload a file at all.

Everything here runs without a database: the repository is replaced with a fake,
and the pure helpers are tested directly. The response models are validated
explicitly, because a service returning a dict the model rejects is a 500 that
only shows up over HTTP.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.domains.personal_learning import models
from src.domains.personal_learning.services import (
    exam_prep_service,
    prep_focus,
    prep_readiness,
)
from src.shared.exceptions import MaigieError

OWNER = "user-owner"
NOW = datetime.now(UTC)


def _topic(topic_id: str, title: str, mastery: float, *, order: int = 0, minutes: int = 30):
    return SimpleNamespace(
        id=topic_id,
        prep_id="prep-1",
        title=title,
        description=f"About {title}",
        category="Foundations",
        estimated_minutes=minutes,
        order_index=order,
        mastery_score=mastery,
        target_mastery=None,
        status="IN_PROGRESS",
        created_at=NOW,
    )


# ---------------------------------------------------------------------------
# The target line
# ---------------------------------------------------------------------------


class TestTargetPercent:
    """The pace line: where readiness needs to be, given a stated target."""

    def test_no_target_means_no_line(self):
        # The point of returning None rather than a default: a target nobody set
        # would put a goal on the learner's chart that they never chose.
        assert (
            prep_readiness.target_percent_on(
                date(2026, 8, 15),
                started_on=date(2026, 8, 1),
                exam_on=date(2026, 8, 31),
                target_readiness=None,
            )
            is None
        )

    def test_halfway_through_is_half_the_target(self):
        assert (
            prep_readiness.target_percent_on(
                date(2026, 8, 11),
                started_on=date(2026, 8, 1),
                exam_on=date(2026, 8, 21),
                target_readiness=80,
            )
            == 40.0
        )

    def test_start_day_expects_nothing_yet(self):
        assert (
            prep_readiness.target_percent_on(
                date(2026, 8, 1),
                started_on=date(2026, 8, 1),
                exam_on=date(2026, 8, 21),
                target_readiness=80,
            )
            == 0.0
        )

    def test_exam_day_expects_the_whole_target(self):
        assert (
            prep_readiness.target_percent_on(
                date(2026, 8, 21),
                started_on=date(2026, 8, 1),
                exam_on=date(2026, 8, 21),
                target_readiness=85,
            )
            == 85.0
        )

    def test_past_the_exam_does_not_exceed_the_target(self):
        # The fraction is clamped, so a snapshot taken after the date does not
        # report a target above 100 or above what the learner asked for.
        assert (
            prep_readiness.target_percent_on(
                date(2026, 9, 30),
                started_on=date(2026, 8, 1),
                exam_on=date(2026, 8, 21),
                target_readiness=85,
            )
            == 85.0
        )

    def test_before_the_start_is_floored_at_zero(self):
        assert (
            prep_readiness.target_percent_on(
                date(2026, 7, 1),
                started_on=date(2026, 8, 1),
                exam_on=date(2026, 8, 21),
                target_readiness=85,
            )
            == 0.0
        )

    def test_a_same_day_exam_expects_the_target_immediately(self):
        # No division by zero, and no negative slope for a target date that has
        # already passed.
        assert (
            prep_readiness.target_percent_on(
                date(2026, 8, 1),
                started_on=date(2026, 8, 1),
                exam_on=date(2026, 8, 1),
                target_readiness=70,
            )
            == 70.0
        )


# ---------------------------------------------------------------------------
# Derived progress
# ---------------------------------------------------------------------------


def _progress(**overrides) -> prep_readiness.PrepProgress:
    defaults = {
        "topics_total": 10,
        "topics_strong": 4,
        "topics_focus": 3,
        "topics_assessed": 6,
        "questions_answered": 20,
        "questions_correct": 15,
        "quizzes_taken": 3,
        "practice_seconds": 900,
        "mastery_sum": 700.0,
    }
    return prep_readiness.PrepProgress(**{**defaults, **overrides})


class TestProgressDerivations:
    def test_the_three_bands_always_sum_to_the_total(self):
        progress = _progress()
        assert (
            progress.topics_strong + progress.topics_review + progress.topics_focus
            == progress.topics_total
        )

    def test_review_is_never_negative(self):
        # Defensive because the band counts come from separate aggregates: a
        # threshold change mid-query must not produce a negative middle band.
        progress = _progress(topics_total=2, topics_strong=2, topics_focus=2)
        assert progress.topics_review == 0

    def test_practice_minutes_floor_partial_minutes(self):
        assert _progress(practice_seconds=119).practice_minutes == 1


# ---------------------------------------------------------------------------
# The next-action recommendation
# ---------------------------------------------------------------------------


class TestFocusRecommendation:
    def test_no_topics_recommends_extraction(self):
        recommendation = prep_focus.recommend([])
        assert recommendation.reason_code == "NO_TOPICS"
        assert recommendation.topic_id is None

    def test_an_unpractised_topic_outranks_a_weak_measured_one(self):
        # Mastery 0 on a topic nobody has been asked about is an absence of
        # evidence, not a bad result, so it is a different recommendation.
        topics = [
            _topic("t-weak", "Regression", 20.0, order=0),
            _topic("t-new", "Bayes", 0.0, order=1),
        ]
        recommendation = prep_focus.recommend(topics, answered_by_topic={"t-weak": 12, "t-new": 0})
        assert recommendation.reason_code == "NEVER_PRACTISED"
        assert recommendation.topic_id == "t-new"
        assert recommendation.recommended_mode == "TOPIC_FOCUS"

    def test_a_preparation_with_no_questions_at_all_is_never_practised(self):
        """Regression: an empty counts mapping is information, not missing data.

        Found by running the migration and reading a real preparation back. Ten
        topics, no banked questions, so the counts mapping came back `{}` — which
        was treated as "no data" and fell through to `LOWEST_MASTERY`, telling the
        learner that "Functions and Graphs is your lowest-scoring topic at 0%".
        They had not scored zero; they had not been asked.
        """
        topics = [_topic("t-a", "Functions and Graphs", 0.0, order=0)]
        recommendation = prep_focus.recommend(topics, answered_by_topic={})
        assert recommendation.reason_code == "NEVER_PRACTISED"
        assert "0%" not in recommendation.reason

    def test_no_counts_supplied_falls_back_to_mastery(self):
        # `None` really is "unknown", and the fallback stays available for callers
        # that have not loaded counts.
        topics = [_topic("t-a", "Functions and Graphs", 40.0)]
        recommendation = prep_focus.recommend(topics)
        assert recommendation.reason_code == "LOWEST_MASTERY"

    def test_the_weakest_measured_topic_is_chosen(self):
        topics = [
            _topic("t-strong", "Probability", 92.0),
            _topic("t-weak", "Hypothesis testing", 58.0),
        ]
        recommendation = prep_focus.recommend(
            topics, answered_by_topic={"t-strong": 40, "t-weak": 12}
        )
        assert recommendation.reason_code == "LOWEST_MASTERY"
        assert recommendation.topic_id == "t-weak"
        assert recommendation.band == "focus"
        # Below the focus boundary the neighbouring topics are usually weak too,
        # so the set is drawn across them rather than pinned to one.
        assert recommendation.recommended_mode == "WEAK_AREAS"
        assert "58" in recommendation.reason

    def test_a_review_band_topic_gets_a_single_topic_drill(self):
        topics = [_topic("t-review", "Confidence intervals", 74.0)]
        recommendation = prep_focus.recommend(topics, answered_by_topic={"t-review": 8})
        assert recommendation.band == "review"
        assert recommendation.recommended_mode == "TOPIC_FOCUS"

    def test_all_strong_recommends_maintenance_not_a_manufactured_weakness(self):
        topics = [
            _topic("t-a", "Probability", 95.0),
            _topic("t-b", "Distributions", 88.0),
        ]
        recommendation = prep_focus.recommend(topics, answered_by_topic={"t-a": 10, "t-b": 10})
        assert recommendation.reason_code == "MAINTENANCE"
        assert recommendation.recommended_mode == "QUICK_REVIEW"

    def test_the_duration_matches_the_set_size_not_the_topic_estimate(self):
        """Regression: a five-question set was advertised as 45 minutes.

        The duration came from the topic's `estimatedMinutes`, which is the time to
        *study* the topic end to end. Someone who sets aside 45 minutes for a
        ten-minute set has been misinformed in the direction that stops them
        practising at all.
        """
        # A topic with a large study estimate must not inflate the set's duration.
        topics = [_topic("t-long", "Calculus", 40.0, minutes=120)]
        recommendation = prep_focus.recommend(topics, answered_by_topic={"t-long": 4})

        assert recommendation.recommended_question_count == 5
        assert recommendation.estimated_minutes == 10

    def test_the_duration_scales_with_the_question_count(self):
        assert prep_focus._estimated_minutes(5) == 10
        assert prep_focus._estimated_minutes(10) == 20
        # Floored, so a very short set never reads as instant.
        assert prep_focus._estimated_minutes(1) == 5

    def test_the_no_topics_case_also_reports_a_sane_duration(self):
        recommendation = prep_focus.recommend([])
        assert recommendation.estimated_minutes == 10

    def test_the_recommendation_validates_against_the_wire_model(self):
        recommendation = prep_focus.recommend([_topic("t", "Topic", 40.0)])
        models.PrepFocusRecommendation(
            topic_id=recommendation.topic_id,
            topic_title=recommendation.topic_title,
            mastery_percent=recommendation.mastery_percent,
            band=recommendation.band,
            reason_code=recommendation.reason_code,
            reason=recommendation.reason,
            recommended_mode=recommendation.recommended_mode,
            recommended_question_count=recommendation.recommended_question_count,
            estimated_minutes=recommendation.estimated_minutes,
        )


# ---------------------------------------------------------------------------
# Preparation detail and topic listing
# ---------------------------------------------------------------------------


class FakeRepo:
    def __init__(self):
        self.preps: dict[tuple[str, str], SimpleNamespace] = {}
        self.topics: dict[str, list[SimpleNamespace]] = {}
        self.topic_counts: dict[str, dict[str, int]] = {}
        self.practice_days: list[date] = []
        self.created_topics: list[dict] = []
        self.materials: list[SimpleNamespace] = []
        self.updated: list[tuple[str, dict]] = []

    def add_prep(self, prep_id: str, user_id: str, **overrides):
        defaults = {
            "id": prep_id,
            "user_id": user_id,
            "subject": "Statistics final",
            "prep_type": "EXAM",
            "exam_date": NOW + timedelta(days=11),
            "description": "Probability, distributions, inference",
            "status": "IN_PROGRESS",
            "confidence": "DEVELOPING",
            "pace": "BALANCED",
            "target_readiness": 85,
            "created_at": NOW - timedelta(days=10),
            "updated_at": NOW,
        }
        self.preps[(prep_id, user_id)] = SimpleNamespace(**{**defaults, **overrides})

    async def find_exam_prep(self, prep_id: str, user_id: str):
        return self.preps.get((prep_id, user_id))

    async def list_prep_topics(self, prep_id: str):
        return self.topics.get(prep_id, [])

    async def get_prep_topic_question_counts(self, prep_ids: list[str]):
        return self.topic_counts

    async def list_practice_days(self, user_id: str, *, since, prep_id=None):
        return self.practice_days

    async def get_prep_progress_aggregates(self, prep_ids, *, strong_threshold, focus_threshold):
        return {
            prep_id: {
                "topics_total": 10,
                "mastery_sum": 700.0,
                "topics_strong": 4,
                "topics_focus": 3,
                "topics_assessed": 6,
                "answers_total": 20,
                "answers_correct": 15,
                "quizzes_completed": 3,
                "practice_seconds": 900,
            }
            for prep_id in prep_ids
        }

    async def create_prep_topic(self, data: dict):
        self.created_topics.append(data)
        return SimpleNamespace(
            id=f"topic-{len(self.created_topics)}",
            prep_id=data["prepId"],
            title=data["title"],
            description=data.get("description"),
            category=data.get("category"),
            estimated_minutes=data.get("estimatedMinutes", 30),
            order_index=data["orderIndex"],
            mastery_score=0.0,
            target_mastery=None,
            status="NOT_STARTED",
            created_at=NOW,
        )

    async def list_prep_materials(self, prep_id: str):
        return self.materials

    async def create_prep_material(self, data: dict):
        material = SimpleNamespace(
            id="material-1",
            prep_id=data["prepId"],
            filename=data["filename"],
            url=data["url"],
            file_type=data.get("fileType"),
            size=data.get("size"),
            extracted_text=data.get("extractedText"),
            category=data.get("category"),
            label=data.get("label"),
            created_at=NOW,
            updated_at=NOW,
        )
        self.materials.append(material)
        return material

    async def update_exam_prep(self, prep_id: str, data: dict):
        self.updated.append((prep_id, data))
        return self.preps.get((prep_id, OWNER))


@pytest.fixture
def repo(monkeypatch):
    fake = FakeRepo()
    monkeypatch.setattr(exam_prep_service, "repo", fake)
    monkeypatch.setattr(prep_readiness, "repo", fake)
    return fake


class TestPreparationDetail:
    @pytest.mark.asyncio
    async def test_detail_carries_progress_and_validates_on_the_wire(self, repo):
        repo.add_prep("prep-1", OWNER)
        repo.topics["prep-1"] = [
            _topic("t-strong", "Probability", 92.0),
            _topic("t-weak", "Hypothesis testing", 58.0),
        ]
        repo.topic_counts = {
            "t-strong": {"question_count": 46, "answered_count": 43},
            "t-weak": {"question_count": 36, "answered_count": 21},
        }
        repo.practice_days = [date.today(), date.today() - timedelta(days=1)]

        payload = await exam_prep_service.get_preparation_detail(user_id=OWNER, prep_id="prep-1")
        detail = models.PrepDetailResponse.model_validate(payload)

        # The whole point of the endpoint: the header numbers come from here now.
        assert detail.progress.progress_percent == 40.0
        assert detail.progress.average_mastery_percent == 70.0
        assert detail.progress.accuracy_percent == 75.0
        assert detail.progress.practice_minutes == 15
        assert detail.progress.practice_ready is True
        assert detail.progress.target_readiness == 85
        assert detail.target_readiness == 85
        assert detail.days_until_exam == 10
        assert detail.focus is not None
        assert detail.focus.topic_id == "t-weak"

    @pytest.mark.asyncio
    async def test_progress_agrees_with_the_shared_helper(self, repo):
        # If these ever diverge, the workspace and the card that linked to it show
        # different numbers for the same preparation — the exact defect this
        # helper exists to prevent.
        repo.add_prep("prep-1", OWNER)
        payload = await exam_prep_service.get_preparation_detail(user_id=OWNER, prep_id="prep-1")
        shared = await prep_readiness.load_for_preparation("prep-1")
        assert payload["progress"]["progressPercent"] == shared.progress_percent
        assert payload["progress"]["averageMasteryPercent"] == shared.average_mastery_percent
        assert payload["progress"]["accuracyPercent"] == shared.accuracy_percent

    @pytest.mark.asyncio
    async def test_a_passed_exam_date_reports_no_days_remaining(self, repo):
        repo.add_prep("prep-1", OWNER, exam_date=NOW - timedelta(days=3))
        payload = await exam_prep_service.get_preparation_detail(user_id=OWNER, prep_id="prep-1")
        assert payload["daysUntilExam"] is None

    @pytest.mark.asyncio
    async def test_no_target_is_none_rather_than_a_default(self, repo):
        repo.add_prep("prep-1", OWNER, target_readiness=None)
        payload = await exam_prep_service.get_preparation_detail(user_id=OWNER, prep_id="prep-1")
        assert payload["targetReadiness"] is None

    @pytest.mark.asyncio
    async def test_another_learners_preparation_is_not_found(self, repo):
        repo.add_prep("prep-1", OWNER)
        with pytest.raises(Exception):
            await exam_prep_service.get_preparation_detail(
                user_id="user-intruder", prep_id="prep-1"
            )


class TestTopicListing:
    @pytest.mark.asyncio
    async def test_topics_carry_band_category_and_counts(self, repo):
        repo.add_prep("prep-1", OWNER)
        repo.topics["prep-1"] = [
            _topic("t-strong", "Probability", 92.0),
            _topic("t-review", "Confidence intervals", 74.0),
            _topic("t-weak", "Hypothesis testing", 58.0),
        ]
        repo.topic_counts = {"t-weak": {"question_count": 36, "answered_count": 21}}

        payload = await exam_prep_service.list_topics(user_id=OWNER, prep_id="prep-1")
        topics = [models.PrepTopicDetail.model_validate(row) for row in payload]

        assert [topic.band for topic in topics] == ["strong", "review", "focus"]
        assert topics[0].category == "Foundations"
        # A topic with no banked questions reports zero rather than being absent.
        assert (topics[0].question_count, topics[0].answered_question_count) == (0, 0)
        assert (topics[2].question_count, topics[2].answered_question_count) == (36, 21)


# ---------------------------------------------------------------------------
# Topic extraction
# ---------------------------------------------------------------------------


class TestTopicExtraction:
    @pytest.mark.asyncio
    async def test_a_failure_raises_instead_of_returning_an_empty_list(self, repo, monkeypatch):
        # Previously this returned [], which reached the client as a 200 and was
        # indistinguishable from "this material contains no topics".
        repo.add_prep("prep-1", OWNER)

        async def boom(*args, **kwargs):
            raise RuntimeError("provider down")

        monkeypatch.setattr(
            "src.domains.personal_learning.services.llm_resilient.generate_content_json", boom
        )

        with pytest.raises(MaigieError) as excinfo:
            await exam_prep_service.extract_topics(user_id=OWNER, prep_id="prep-1")
        assert excinfo.value.code == "PREP_TOPIC_EXTRACTION_FAILED"
        assert excinfo.value.status_code == 503

    @pytest.mark.asyncio
    async def test_a_payload_with_nothing_usable_is_a_failure(self, repo, monkeypatch):
        repo.add_prep("prep-1", OWNER)

        async def nonsense(*args, **kwargs):
            return [{"description": "no title here"}, "not an object"]

        monkeypatch.setattr(
            "src.domains.personal_learning.services.llm_resilient.generate_content_json", nonsense
        )

        with pytest.raises(MaigieError):
            await exam_prep_service.extract_topics(user_id=OWNER, prep_id="prep-1")

    @pytest.mark.asyncio
    async def test_categories_are_persisted_and_normalized(self, repo, monkeypatch):
        repo.add_prep("prep-1", OWNER)

        async def topics(*args, **kwargs):
            return [
                {"title": "Probability rules", "category": "  Foundations  "},
                {"title": "Regression", "category": "x" * 200},
                {"title": "Bayes", "category": 42},
            ]

        monkeypatch.setattr(
            "src.domains.personal_learning.services.llm_resilient.generate_content_json", topics
        )

        created = await exam_prep_service.extract_topics(user_id=OWNER, prep_id="prep-1")
        categories = [row["category"] for row in created]
        # Trimmed when usable; dropped rather than truncated when not, because half
        # a heading is worse than none once the client renders it.
        assert categories == ["Foundations", None, None]
        assert all(models.PrepTopicResponse.model_validate(row) for row in created)


# ---------------------------------------------------------------------------
# Material upload
# ---------------------------------------------------------------------------


class FakeUpload:
    def __init__(self, content: bytes, filename: str, content_type: str | None = None):
        self._content = content
        self.filename = filename
        self.content_type = content_type

    async def read(self) -> bytes:
        return self._content


class TestSafeFilename:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("notes.pdf", "notes.pdf"),
            # Only the basename survives, so a crafted name cannot escape the
            # preparation's own storage prefix.
            ("../../other-user/notes.pdf", "notes.pdf"),
            ("C:\\Users\\me\\notes.pdf", "notes.pdf"),
            ("my lecture notes.pdf", "my_lecture_notes.pdf"),
            ("", "material"),
            (None, "material"),
        ],
    )
    def test_names_are_reduced_to_a_safe_segment(self, raw, expected):
        assert exam_prep_service._safe_filename(raw) == expected

    def test_a_name_cannot_contain_a_separator(self):
        assert "/" not in exam_prep_service._safe_filename("a/b/c/../../d.pdf")


class TestUploadTextExtraction:
    def test_plain_text_is_extracted(self):
        text = exam_prep_service._extract_upload_text(
            b"Chapter 1: probability", "notes.txt", "text/plain"
        )
        assert text == "Chapter 1: probability"

    def test_an_unreadable_format_is_none_not_an_error(self):
        # An image is still worth storing; it just contributes nothing to topic
        # extraction, and `hasExtractedText` is how the client learns that.
        assert (
            exam_prep_service._extract_upload_text(b"\x89PNG\r\n", "diagram.png", "image/png")
            is None
        )

    def test_a_pdf_without_a_text_layer_is_none_not_an_error(self):
        assert (
            exam_prep_service._extract_upload_text(
                b"not really a pdf", "scan.pdf", "application/pdf"
            )
            is None
        )


class TestMaterialUpload:
    @pytest.mark.asyncio
    async def test_an_empty_file_is_rejected(self, repo):
        repo.add_prep("prep-1", OWNER)
        with pytest.raises(MaigieError) as excinfo:
            await exam_prep_service.upload_material_file(
                user_id=OWNER, prep_id="prep-1", file=FakeUpload(b"", "empty.txt")
            )
        assert excinfo.value.code == "MATERIAL_FILE_EMPTY"

    @pytest.mark.asyncio
    async def test_an_oversized_file_is_rejected_before_storage(self, repo, monkeypatch):
        repo.add_prep("prep-1", OWNER)
        oversized = b"x" * (exam_prep_service.MAX_MATERIAL_UPLOAD_BYTES + 1)

        async def must_not_be_called(*args, **kwargs):
            raise AssertionError("storage was called for an oversized file")

        monkeypatch.setattr(
            "src.shared.infrastructure.storage.storage_service.upload_bytes", must_not_be_called
        )

        with pytest.raises(MaigieError) as excinfo:
            await exam_prep_service.upload_material_file(
                user_id=OWNER, prep_id="prep-1", file=FakeUpload(oversized, "big.txt")
            )
        assert excinfo.value.code == "MATERIAL_FILE_TOO_LARGE"

    @pytest.mark.asyncio
    async def test_a_stored_file_becomes_material_with_extracted_text(self, repo, monkeypatch):
        repo.add_prep("prep-1", OWNER, status="SETUP")
        captured: dict = {}

        async def fake_upload(content, remote_path, *, content_type="application/octet-stream"):
            captured["path"] = remote_path
            return {"url": f"https://cdn.example/{remote_path}", "path": remote_path}

        monkeypatch.setattr(
            "src.shared.infrastructure.storage.storage_service.upload_bytes", fake_upload
        )

        material = await exam_prep_service.upload_material_file(
            user_id=OWNER,
            prep_id="prep-1",
            file=FakeUpload(b"Hypothesis testing notes", "my notes.txt", "text/plain"),
            category="NOTES",
        )

        # Pathed under the learner and the preparation, with a sanitised name, so
        # one learner's upload can never overwrite another's.
        assert captured["path"] == f"prep-materials/{OWNER}/prep-1/my_notes.txt"
        assert material.extracted_text == "Hypothesis testing notes"
        assert material.category == "NOTES"
        # A preparation leaves SETUP once it has material to work from.
        assert ("prep-1", {"status": "IN_PROGRESS"}) in repo.updated

    @pytest.mark.asyncio
    async def test_another_learners_preparation_cannot_be_uploaded_to(self, repo):
        repo.add_prep("prep-1", OWNER)
        with pytest.raises(Exception):
            await exam_prep_service.upload_material_file(
                user_id="user-intruder",
                prep_id="prep-1",
                file=FakeUpload(b"content", "notes.txt", "text/plain"),
            )
