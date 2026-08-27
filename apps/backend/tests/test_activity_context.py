"""The activity feed's routeable artifact pair.

Every writer of the feed already put an id in `context`, and each chose its own key — `noteId`,
`docId`, `cardId`, `prepId`, `quizId`, `planId`. So a reader that wanted to make an entry clickable
had to know all six names, and the seventh writer would have invented a seventh. `entityType` and
`entityId` are now required arguments to `record`, which is what makes them dependable: an optional
field here would be filled in by the six callers that exist and forgotten by the next one, which is
exactly how six key names happened in the first place.

No database. These assert the shape of what is written and what is published, which is where the
guarantee lives.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

import inspect
from datetime import UTC, datetime

import pytest

from src.domains.personal_learning import models
from src.domains.personal_learning.services import activity_feed_service

OWNER = "user-owner"
NOW = datetime.now(UTC)


@pytest.fixture
def written(monkeypatch):
    """Captures what `record` hands the repository."""
    rows: list[dict] = []

    class FakeRepo:
        async def create_feed_entry(self, data):
            rows.append(data)
            return type("Entry", (), data)

    monkeypatch.setattr(activity_feed_service, "repo", FakeRepo())

    async def no_streak(_user_id):
        return 0

    monkeypatch.setattr(activity_feed_service, "_compute_current_streak", no_streak)
    return rows


class TestRecording:
    async def test_the_pair_is_written_into_the_context(self, written):
        await activity_feed_service.record(
            user_id=OWNER,
            activity_type="note_created",
            title="Created note: Vectors",
            entity_type="note",
            entity_id="note-1",
            context={"source": "personal", "noteId": "note-1"},
        )
        context = written[0]["context"]
        assert context["entityType"] == "note"
        assert context["entityId"] == "note-1"

    async def test_the_service_specific_key_is_kept(self, written):
        """Historical rows already carry it, and dropping it would make old and new rows disagree."""
        await activity_feed_service.record(
            user_id=OWNER,
            activity_type="document_generated",
            title="Generated essay",
            entity_type="document",
            entity_id="doc-1",
            context={"source": "personal", "docId": "doc-1", "format": "pdf"},
        )
        context = written[0]["context"]
        assert context["docId"] == "doc-1"
        assert context["format"] == "pdf"
        assert context["entityId"] == "doc-1"

    async def test_it_works_with_no_context_at_all(self, written):
        await activity_feed_service.record(
            user_id=OWNER,
            activity_type="quiz_completed",
            title="Completed quiz",
            entity_type="quiz",
            entity_id="quiz-1",
        )
        assert written[0]["context"] == {"entityType": "quiz", "entityId": "quiz-1"}

    def test_both_arguments_are_required(self):
        """The reason the guarantee holds. An optional pair would decay to the old situation."""
        signature = inspect.signature(activity_feed_service.record)
        for name in ("entity_type", "entity_id"):
            assert signature.parameters[name].default is inspect.Parameter.empty

    def test_every_writer_in_the_domain_supplies_them(self):
        """A textual check, so it also catches writers added after this test.

        The signature makes a missing argument a `TypeError` at runtime; this makes it a failing test
        at review time, which is the cheaper of the two places to find out.
        """
        import pathlib
        import re

        domain = pathlib.Path("src/domains/personal_learning/services")
        calls = 0
        for path in domain.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            for match in re.finditer(r"activity_feed_service\.record\((.*?)\)\n", source, re.S):
                calls += 1
                arguments = match.group(1)
                assert "entity_type=" in arguments, f"{path.name} records without an entity type"
                assert "entity_id=" in arguments, f"{path.name} records without an entity id"
        # Eight call sites across seven services — `exam_prep_service` records both the start and the
        # completion of a preparation, and `prep_outcome_service` records the post-exam review. The count
        # is asserted so that a writer *removed* is noticed too: a feed quietly losing a kind of entry is
        # the failure this area keeps producing.
        assert calls == 8


def _entry(context: dict | None) -> models.ActivityFeedEntryResponse:
    return models.ActivityFeedEntryResponse(
        id="a-1",
        user_id=OWNER,
        activity_type="note_created",
        title="Created note",
        context=context,
        occurred_at=NOW,
    )


class TestPublishing:
    def test_the_pair_is_published_as_typed_fields(self):
        """`context` is an opaque dict in the schema, so a client cannot rely on what is inside it.

        Promoting the two fields is what puts them in the generated types.
        """
        entry = _entry({"source": "personal", "entityType": "note", "entityId": "note-1"})
        assert entry.entity_type == "note"
        assert entry.entity_id == "note-1"

    @pytest.mark.parametrize(
        ("key", "expected_type"),
        [
            ("noteId", "note"),
            ("docId", "document"),
            ("cardId", "flashcard"),
            ("planId", "study_plan"),
            ("prepId", "preparation"),
            ("quizId", "quiz"),
        ],
    )
    def test_an_entry_written_before_the_pair_existed_is_still_routeable(self, key, expected_type):
        """The id in those rows genuinely is an id of that kind, so deriving it is reading, not guessing."""
        entry = _entry({"source": "personal", key: "row-1"})
        assert entry.entity_type == expected_type
        assert entry.entity_id == "row-1"

    def test_an_entry_with_nothing_to_point_at_reports_nothing(self):
        """Rendered as text, which is what it is. A fabricated target would be a link to nowhere."""
        entry = _entry({"source": "personal"})
        assert entry.entity_type is None
        assert entry.entity_id is None

    def test_a_missing_context_is_not_an_error(self):
        entry = _entry(None)
        assert entry.entity_id is None

    def test_the_explicit_pair_wins_over_the_legacy_key(self):
        """A plan item completion records the *plan*, because an item has no page of its own."""
        entry = _entry(
            {
                "planId": "plan-1",
                "itemId": "item-1",
                "entityType": "study_plan",
                "entityId": "plan-1",
            }
        )
        assert entry.entity_type == "study_plan"
        assert entry.entity_id == "plan-1"

    def test_the_pair_reaches_the_wire_in_camel_case(self):
        entry = _entry({"entityType": "note", "entityId": "note-1"})
        wire = entry.model_dump(by_alias=True)
        assert wire["entityType"] == "note"
        assert wire["entityId"] == "note-1"
        assert "entity_id" not in wire
