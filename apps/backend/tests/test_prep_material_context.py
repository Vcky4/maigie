"""Tests for how much of a learner's material reaches the model, and which of it.

The path this replaces had **no test coverage at all** — renaming its only helper broke
nothing, which is how it went unnoticed that a 162,885-character document contributed
5,000 characters (3.1%) to topic extraction, that a second file could contribute
nothing, and that exam simulation ignored the category it was supposedly built on.

Four properties are asserted here because each was a real defect:

1. Every file with something to say gets a slot. Truncating a *joined* string meant
   file two was invisible behind file one.
2. Category decides reading order, and can restrict the pool. A syllabus states what
   is examinable; it should not lose to a textbook that happens to be newer.
3. The budget is respected exactly, so prompt size stays predictable — quiz generation
   already sits at a measured p50 of 16.3s against a 60s timeout.
4. Restricting by category falls back rather than refusing, so a learner who did not
   label their upload is not punished for it.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from types import SimpleNamespace

import pytest

from src.domains.personal_learning.services import prep_material_context as ctx

_USE_CHARS = object()


def _material(filename: str, *, category: str = "OTHER", chars: int = 1000, text=_USE_CHARS):
    """A material row. `text=None` means a scanned file with no text layer, which is
    the state the database actually holds and is different from an empty string."""
    body = ("x" * chars) if text is _USE_CHARS else text
    return SimpleNamespace(filename=filename, category=category, extracted_text=body)


class TestEveryFileGetsHeard:
    def test_a_second_file_is_not_starved_by_a_long_first_one(self):
        """The defect this module exists for.

        Extraction used to join every material and cut the join, so a syllabus behind
        a textbook contributed nothing — and a syllabus is the most useful thing to
        read and the most likely to be uploaded second.
        """
        result = ctx.select(
            [
                _material("textbook.pdf", category="TEXTBOOK", chars=500_000),
                _material("syllabus.pdf", category="SYLLABUS", chars=4_000),
            ],
            budget=10_000,
        )

        read = {excerpt.filename for excerpt in result.excerpts}
        assert read == {"textbook.pdf", "syllabus.pdf"}
        assert result.omitted == []

    def test_no_single_file_takes_the_whole_budget(self):
        result = ctx.select(
            [_material("a.pdf", chars=500_000), _material("b.pdf", chars=500_000)],
            budget=10_000,
        )
        for excerpt in result.excerpts:
            assert len(excerpt.text) <= int(10_000 * ctx.MAX_SHARE_PER_FILE) + ctx.MIN_USEFUL_CHARS

    def test_many_files_each_get_a_usable_share(self):
        materials = [_material(f"f{i}.pdf", chars=100_000) for i in range(6)]
        result = ctx.select(materials, budget=12_000)

        assert len(result.excerpts) == 6
        for excerpt in result.excerpts:
            assert len(excerpt.text) >= ctx.MIN_USEFUL_CHARS

    def test_the_last_file_may_use_what_is_left(self):
        """There is nobody left to reserve for, so leaving a remainder unspent would
        waste budget the learner's material could have filled."""
        result = ctx.select(
            [_material("a.pdf", chars=1_000), _material("b.pdf", chars=500_000)],
            budget=10_000,
        )
        assert result.used_chars > 9_000

    @pytest.mark.parametrize("empty", [None, ""])
    def test_a_file_with_no_text_is_skipped_not_counted(self, empty):
        """A scanned PDF stores fine and has no text layer. It must not occupy a slot,
        and it must not count towards `storedChars` either — the gap between stored and
        used is meant to describe readable material."""
        result = ctx.select(
            [_material("scan.pdf", text=empty), _material("notes.md", chars=500)],
            budget=10_000,
        )
        assert [e.filename for e in result.excerpts] == ["notes.md"]
        assert result.omitted == []
        assert result.stored_chars == 500

    def test_whitespace_only_text_is_treated_as_no_text(self):
        result = ctx.select([_material("blank.txt", text="   \n\n  ")], budget=10_000)
        assert not result.has_text


class TestCategoryDecidesPriority:
    def test_a_syllabus_is_read_before_a_textbook(self):
        """Order is by value per character, not by upload time. A syllabus defines
        what is examinable; a textbook is where the answers happen to live."""
        result = ctx.select(
            [
                _material("textbook.pdf", category="TEXTBOOK", chars=5_000),
                _material("syllabus.pdf", category="SYLLABUS", chars=5_000),
            ],
            budget=20_000,
        )
        assert [e.filename for e in result.excerpts][0] == "syllabus.pdf"

    def test_past_questions_outrank_notes(self):
        result = ctx.select(
            [
                _material("notes.md", category="NOTES", chars=5_000),
                _material("paper.pdf", category="PAST_QUESTION", chars=5_000),
            ],
            budget=20_000,
        )
        assert [e.filename for e in result.excerpts][0] == "paper.pdf"

    def test_an_unknown_category_sorts_last_without_raising(self):
        result = ctx.select(
            [
                _material("weird.bin", category="SOMETHING_NEW", chars=1_000),
                _material("syllabus.pdf", category="SYLLABUS", chars=1_000),
            ],
            budget=20_000,
        )
        assert [e.filename for e in result.excerpts] == ["syllabus.pdf", "weird.bin"]

    def test_a_missing_category_is_treated_as_other(self):
        material = SimpleNamespace(filename="x.txt", category=None, extracted_text="y" * 500)
        result = ctx.select([material], budget=10_000)
        assert result.excerpts[0].category == "OTHER"

    def test_order_within_a_category_follows_the_query(self):
        """The repository returns `createdAt DESC`, so the newest of two syllabi wins.
        Category decides the group; recency decides inside it."""
        result = ctx.select(
            [
                _material("newer.pdf", category="SYLLABUS", chars=1_000),
                _material("older.pdf", category="SYLLABUS", chars=1_000),
            ],
            budget=20_000,
        )
        assert [e.filename for e in result.excerpts] == ["newer.pdf", "older.pdf"]


class TestCategoryRestriction:
    def test_exam_simulation_can_restrict_the_pool(self):
        """The defect: `PAST_PAPER_SIM` ignored category entirely and grounded itself
        in whatever was uploaded most recently."""
        result = ctx.select(
            [
                _material("textbook.pdf", category="TEXTBOOK", chars=5_000),
                _material("paper.pdf", category="PAST_QUESTION", chars=5_000),
            ],
            budget=20_000,
            categories=("PAST_QUESTION", "SYLLABUS"),
        )
        assert [e.filename for e in result.excerpts] == ["paper.pdf"]

    def test_restriction_falls_back_when_nothing_matches(self):
        """A learner who uploaded a paper without labelling it should still get a
        grounded simulation. Refusing would punish them for a category they were never
        required to set."""
        result = ctx.select(
            [_material("unlabelled.pdf", category="OTHER", chars=5_000)],
            budget=20_000,
            categories=("PAST_QUESTION",),
        )
        assert [e.filename for e in result.excerpts] == ["unlabelled.pdf"]

    def test_restriction_is_case_insensitive(self):
        result = ctx.select(
            [_material("paper.pdf", category="past_question", chars=1_000)],
            budget=10_000,
            categories=("PAST_QUESTION",),
        )
        assert result.has_text


class TestBudget:
    @pytest.mark.parametrize("budget", [1_000, 5_000, 12_000, 24_000, 30_000])
    def test_the_budget_is_never_exceeded(self, budget):
        materials = [_material(f"f{i}.pdf", chars=200_000) for i in range(4)]
        result = ctx.select(materials, budget=budget)
        assert result.used_chars <= budget

    def test_a_zero_budget_reads_nothing(self):
        result = ctx.select([_material("a.pdf", chars=5_000)], budget=0)
        assert not result.has_text
        assert result.used_chars == 0
        # Still reports what exists, so a caller can say how much went unread.
        assert result.stored_chars == 5_000

    def test_no_materials_is_empty_not_an_error(self):
        result = ctx.select([], budget=10_000)
        assert not result.has_text
        assert (result.stored_chars, result.used_chars) == (0, 0)

    def test_material_smaller_than_the_budget_is_read_whole(self):
        result = ctx.select([_material("short.md", chars=800)], budget=24_000)
        assert result.used_chars == 800
        assert result.excerpts[0].truncated is False

    def test_truncation_is_reported(self):
        result = ctx.select([_material("long.pdf", chars=100_000)], budget=10_000)
        assert result.excerpts[0].truncated is True
        assert result.excerpts[0].stored_chars == 100_000

    def test_the_measured_shortfall_is_visible(self):
        """The number that started this: 162,885 stored, 5,000 used. A caller logs
        both so the gap is observable rather than inferred from a prompt."""
        result = ctx.select(
            [_material("big.pdf", chars=162_885)],
            budget=ctx.TOPIC_EXTRACTION_BUDGET,
        )
        assert result.stored_chars == 162_885
        assert result.used_chars == ctx.TOPIC_EXTRACTION_BUDGET
        # Nearly five times what the old 5,000-character cap allowed.
        assert result.used_chars / 5_000 > 4


class TestPromptBlock:
    def test_each_excerpt_is_labelled_with_its_category(self):
        """The category tells the model what the document *is*: a syllabus defines
        scope, a past paper demonstrates question style, a textbook supplies facts.
        Unlabelled, they are an undifferentiated wall of text."""
        result = ctx.select(
            [
                _material("syllabus.pdf", category="SYLLABUS", text="scope here"),
                _material("paper.pdf", category="PAST_QUESTION", text="question here"),
            ],
            budget=20_000,
        )
        block = result.as_prompt_block()
        assert "[SYLLABUS · syllabus.pdf]" in block
        assert "[PAST_QUESTION · paper.pdf]" in block
        assert "scope here" in block

    def test_an_empty_context_produces_an_empty_block(self):
        assert ctx.select([], budget=10_000).as_prompt_block() == ""


class TestBudgetsAreProportionate:
    def test_every_budget_is_well_above_the_old_cap(self):
        """5,000 characters was ~1% of the smallest configured context window."""
        for budget in (
            ctx.TOPIC_EXTRACTION_BUDGET,
            ctx.PAST_PAPER_BUDGET,
            ctx.QUESTION_GROUNDING_BUDGET,
        ):
            assert budget >= 12_000

    def test_exam_simulation_reads_the_most(self):
        """Its questions must come from the document, not merely be informed by it."""
        assert ctx.PAST_PAPER_BUDGET >= ctx.TOPIC_EXTRACTION_BUDGET
        assert ctx.PAST_PAPER_BUDGET > ctx.QUESTION_GROUNDING_BUDGET

    def test_budgets_stay_inside_a_128k_context(self):
        """Bounded by latency, not just by the model. Generation already sits at a
        measured p50 of 16.3s against a 60s server timeout, so prompt size is not
        free — roughly 4 characters per token, and room is needed for the
        instructions, the topic list and the response."""
        largest_tokens = ctx.PAST_PAPER_BUDGET / 4
        assert largest_tokens < 128_000 * 0.1
