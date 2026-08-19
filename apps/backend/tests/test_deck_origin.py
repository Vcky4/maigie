"""Origin-scoped decks: the get-or-create, and the backfill's routing table.

Both exist to stop generated flashcards being created with ``deckId = NULL``. That state
is invisible in the flashcards dashboard — its deck list is a ``LEFT JOIN`` from
``FlashcardDeck``, so a null ``deckId`` matches no row — while the header counts read
straight from ``Flashcard`` and do include it, so a learner saw cards due with no deck
holding them.

No database. ``ensure_deck_for_origin`` is exercised against a fake repository, which is
what lets the race path be tested at all: provoking a real unique violation between a
lookup and an insert is not something an integration test can arrange reliably.
"""

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

import pytest
from sqlalchemy.exc import IntegrityError

from src.domains.personal_learning.services import flashcard_service as fs

# The backfill lives in `scripts/`, which is not a package on the import path.
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import backfill_deck_origins as backfill  # noqa: E402

# ---------------------------------------------------------------------------
# ensure_deck_for_origin
# ---------------------------------------------------------------------------


class FakeDeck:
    """Every attribute `_deck_payload` reads.

    Deliberately the full set rather than a stub with `id` only: the payload builder is
    what would drop a newly added column silently, and the first version of this fake
    caught exactly that — `originType` and `originId` reached the response model without
    being mapped, so they serialised as null for every deck.
    """

    def __init__(self, deck_id: str, *, user_id="u1", origin_type=None, origin_id=None):
        self.id = deck_id
        self.user_id = user_id
        self.title = "Fake deck"
        self.description = None
        self.subject = None
        self.accent = None
        self.daily_goal = None
        self.course_id = None
        self.topic_id = None
        self.prep_id = None
        self.origin_type = origin_type
        self.origin_id = origin_id
        self.created_at = datetime(2026, 8, 18, tzinfo=UTC)
        self.updated_at = datetime(2026, 8, 18, tzinfo=UTC)


class FakeRepo:
    """A repository that records calls, and can be told to behave badly.

    ``existing`` is keyed the same way the partial unique index is, so the fake enforces
    the same constraint the database does.
    """

    def __init__(self, existing: dict[tuple[str, str, str], str] | None = None):
        self.existing = dict(existing or {})
        self.create_calls: list[dict] = []
        #: Set to raise on the next create, simulating a concurrent writer winning.
        self.raise_on_create: Exception | None = None
        #: What the losing racer should find when it re-reads.
        self.appears_after_conflict: str | None = None
        self.lookup_count = 0

    async def find_deck_by_origin(self, user_id, origin_type, origin_id):
        self.lookup_count += 1
        deck_id = self.existing.get((user_id, origin_type, origin_id))
        return FakeDeck(deck_id) if deck_id else None

    async def create_deck(self, data):
        if self.raise_on_create is not None:
            error = self.raise_on_create
            self.raise_on_create = None
            if self.appears_after_conflict:
                key = (data["userId"], data["originType"], data["originId"])
                self.existing[key] = self.appears_after_conflict
            raise error
        self.create_calls.append(data)
        new_id = f"deck-{len(self.create_calls)}"
        self.existing[(data["userId"], data["originType"], data["originId"])] = new_id
        return FakeDeck(
            new_id,
            user_id=data["userId"],
            origin_type=data["originType"],
            origin_id=data["originId"],
        )


def _integrity_error() -> IntegrityError:
    return IntegrityError("INSERT ...", {}, Exception("duplicate key value"))


@pytest.fixture
def fake_repo(monkeypatch):
    repo = FakeRepo()
    monkeypatch.setattr(fs, "repo", repo)
    return repo


class TestEnsureDeckForOrigin:
    @pytest.mark.asyncio
    async def test_creates_a_deck_on_first_use(self, fake_repo):
        deck_id = await fs.ensure_deck_for_origin(
            user_id="u1",
            origin_type=fs.DECK_ORIGIN_NOTE,
            origin_id="note1",
            title="Photosynthesis — cards",
        )
        assert deck_id == "deck-1"
        assert len(fake_repo.create_calls) == 1
        created = fake_repo.create_calls[0]
        assert created["originType"] == "note"
        assert created["originId"] == "note1"
        assert created["title"] == "Photosynthesis — cards"

    @pytest.mark.asyncio
    async def test_reuses_the_same_deck_on_a_second_generation(self, fake_repo):
        """The whole point: pressing Generate twice adds to one deck, not two.

        Cards used to be created with `deckId = None` every time, so there was nothing to
        reuse and nothing to find.
        """
        first = await fs.ensure_deck_for_origin(
            user_id="u1", origin_type=fs.DECK_ORIGIN_NOTE, origin_id="note1", title="A"
        )
        second = await fs.ensure_deck_for_origin(
            user_id="u1", origin_type=fs.DECK_ORIGIN_NOTE, origin_id="note1", title="A"
        )
        assert first == second
        assert len(fake_repo.create_calls) == 1

    @pytest.mark.asyncio
    async def test_does_not_rename_a_deck_the_learner_already_has(self, fake_repo):
        """`title` applies only at creation.

        Re-titling on every call would undo a rename the learner made, and would also
        rename their deck behind them whenever the source note was renamed.
        """
        fake_repo.existing[("u1", "note", "note1")] = "deck-existing"
        deck_id = await fs.ensure_deck_for_origin(
            user_id="u1",
            origin_type=fs.DECK_ORIGIN_NOTE,
            origin_id="note1",
            title="A title the learner did not choose",
        )
        assert deck_id == "deck-existing"
        assert fake_repo.create_calls == []

    @pytest.mark.asyncio
    async def test_the_created_deck_publishes_its_origin(self, fake_repo):
        """`_deck_payload` must map the origin, not just accept it.

        This is a regression guard. The columns and the response model were added first
        and the payload builder was not updated, so every deck reported a null origin —
        which would have left the clients unable to tell a generated deck from a
        hand-made one, or to find the deck for a note.
        """
        deck = await fs.create_deck(
            user_id="u1",
            data={"title": "T", "originType": fs.DECK_ORIGIN_NOTE, "originId": "note1"},
        )
        assert deck["originType"] == "note"
        assert deck["originId"] == "note1"

    @pytest.mark.asyncio
    async def test_separate_origins_get_separate_decks(self, fake_repo):
        note_deck = await fs.ensure_deck_for_origin(
            user_id="u1", origin_type=fs.DECK_ORIGIN_NOTE, origin_id="x", title="note"
        )
        topic_deck = await fs.ensure_deck_for_origin(
            user_id="u1", origin_type=fs.DECK_ORIGIN_TOPIC, origin_id="x", title="topic"
        )
        assert note_deck != topic_deck

    @pytest.mark.asyncio
    async def test_the_same_origin_for_two_learners_does_not_collide(self, fake_repo):
        mine = await fs.ensure_deck_for_origin(
            user_id="u1", origin_type=fs.DECK_ORIGIN_TOPIC, origin_id="t1", title="t"
        )
        theirs = await fs.ensure_deck_for_origin(
            user_id="u2", origin_type=fs.DECK_ORIGIN_TOPIC, origin_id="t1", title="t"
        )
        assert mine != theirs

    @pytest.mark.asyncio
    async def test_losing_a_race_uses_the_winners_deck(self, fake_repo):
        """Two generations for the same note can both miss the lookup.

        The partial unique index stops the second insert, and this call absorbs the
        violation and re-reads rather than surfacing an error for something that has
        already been done correctly by someone else.
        """
        fake_repo.raise_on_create = _integrity_error()
        fake_repo.appears_after_conflict = "deck-from-winner"

        deck_id = await fs.ensure_deck_for_origin(
            user_id="u1", origin_type=fs.DECK_ORIGIN_NOTE, origin_id="note1", title="A"
        )
        assert deck_id == "deck-from-winner"
        assert fake_repo.lookup_count == 2  # the miss, then the re-read

    @pytest.mark.asyncio
    async def test_an_unrelated_integrity_error_is_not_swallowed(self, fake_repo):
        """Only the origin conflict is ours to absorb.

        If the re-read finds nothing, the violation was something else — a bad user id,
        say — and hiding it would turn a real fault into a silent one.
        """
        fake_repo.raise_on_create = _integrity_error()
        fake_repo.appears_after_conflict = None

        with pytest.raises(IntegrityError):
            await fs.ensure_deck_for_origin(
                user_id="u1", origin_type=fs.DECK_ORIGIN_NOTE, origin_id="note1", title="A"
            )


# ---------------------------------------------------------------------------
# Backfill routing
# ---------------------------------------------------------------------------


def place(source_type, source_id, **lookups):
    defaults = {
        "note_titles": {},
        "topic_titles": {},
        "prep_titles": {},
        "live_decks": set(),
        "item_to_review_deck": {},
    }
    defaults.update(lookups)
    return backfill.decide_placement(source_type, source_id, **defaults)


class TestBackfillRouting:
    def test_note_cards_get_a_note_deck(self):
        result = place("note", "n1", note_titles={"n1": "Krebs cycle"})
        assert result["origin_type"] == "note"
        assert result["origin_id"] == "n1"
        assert result["deck_title"] == "Krebs cycle — cards"
        assert result["skip_reason"] is None

    def test_topic_cards_get_a_topic_deck(self):
        result = place("topic", "t1", topic_titles={"t1": "Recursion"})
        assert result["origin_type"] == "topic"
        assert result["deck_title"] == "Recursion — cards"

    def test_cards_whose_source_was_deleted_stay_unfiled(self):
        """Declining is a real outcome. Inventing a deck named after a note that no
        longer exists would file cards under something the learner cannot open."""
        assert place("note", "gone")["skip_reason"] == "note no longer exists"
        assert place("topic", "gone")["skip_reason"] == "topic no longer exists"

    def test_onboarding_cards_with_a_prep_id_get_a_prep_deck(self):
        result = place("auto_setup", "p1", prep_titles={"p1": "Organic Chemistry"})
        assert result["origin_type"] == "prep"
        assert result["deck_title"] == "Organic Chemistry — starter cards"

    def test_legacy_onboarding_cards_are_filed_under_the_subject_text(self):
        """Old rows stored the typed subject, not an id.

        Filed under the text rather than title-matched against preparations, because a
        match would be a guess and this is data we cannot re-derive.
        """
        result = place("auto_setup", "Biology")
        assert result["origin_type"] == "subject"
        assert result["origin_id"] == "Biology"
        assert result["deck_title"] == "Biology — starter cards"

    def test_starter_cards_go_back_to_their_deck_when_it_survives(self):
        result = place("deck_starter", "d1", live_decks={"d1"})
        assert result["existing_deck_id"] == "d1"
        assert result["origin_type"] is None  # an existing deck needs no new origin

    def test_starter_cards_for_a_deleted_deck_stay_unfiled(self):
        """The learner deleted that deck on purpose; re-creating it would undo them."""
        result = place("deck_starter", "d1", live_decks=set())
        assert result["skip_reason"] == "the deck these were made for was deleted"

    def test_plan_item_cards_use_the_plans_review_deck(self):
        result = place("study_plan_item", "i1", item_to_review_deck={"i1": "deck-review"})
        assert result["existing_deck_id"] == "deck-review"
        assert result["origin_type"] is None

    def test_plan_item_cards_without_a_review_deck_stay_unfiled(self):
        assert place("study_plan_item", "i1")["skip_reason"] == "plan has no review deck"

    def test_a_group_with_no_source_id_cannot_be_grouped(self):
        assert place("note", None)["skip_reason"] == "no sourceId to group on"

    def test_an_unknown_source_type_is_reported_not_guessed(self):
        result = place("something_new", "x")
        assert "unrecognised sourceType" in result["skip_reason"]

    @pytest.mark.parametrize(
        "source_type",
        ["note", "topic", "auto_setup", "deck_starter", "study_plan_item", "mystery"],
    )
    def test_every_source_kind_produces_a_decision(self, source_type):
        """Either a placement or a stated reason — never both, and never neither."""
        result = place(source_type, "some-id")
        placed = result["origin_type"] is not None or result["existing_deck_id"] is not None
        assert placed != (result["skip_reason"] is not None)
