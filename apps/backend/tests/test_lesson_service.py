"""Parsing a generated lesson.

These are pure-function tests and they carry more weight than their size suggests: the model reply is
the one input to this feature that no type system guards. The route asks for a shape and receives
something close to it, so every assertion here is a specific way a real reply has been observed to
fall short, and what the learner would have seen if it were stored as-is.
"""

from src.domains.knowledge.services import lesson_service


def _section(**overrides):
    base = {
        "title": "Breadth-first search",
        "kind": "concept",
        "paragraphs": ["A queue explores the graph in distance layers."],
    }
    return {**base, **overrides}


class TestSections:
    def test_a_well_formed_reply_becomes_ordered_rows(self):
        parsed = lesson_service.parse_lesson(
            {"sections": [_section(title="First"), _section(title="Second")]}
        )

        assert [s["title"] for s in parsed["sections"]] == ["First", "Second"]
        # Spaced rather than consecutive, so a section can later be inserted between two without
        # renumbering every row after it.
        assert [s["order"] for s in parsed["sections"]] == [10.0, 20.0]

    def test_a_section_without_paragraphs_is_dropped(self):
        """It would render as a clickable heading with nothing under it."""
        parsed = lesson_service.parse_lesson(
            {"sections": [_section(), _section(title="Empty", paragraphs=[])]}
        )
        assert [s["title"] for s in parsed["sections"]] == ["Breadth-first search"]

    def test_a_section_without_a_title_is_dropped(self):
        """It would appear in the outline as a blank row the learner cannot identify."""
        parsed = lesson_service.parse_lesson({"sections": [_section(title="   ")]})
        assert parsed["sections"] == []

    def test_an_unknown_kind_falls_back_to_concept(self):
        """Rather than storing a kind the reader has no styling for and rendering it unstyled."""
        parsed = lesson_service.parse_lesson({"sections": [_section(kind="interpretive-dance")]})
        assert parsed["sections"][0]["kind"] == "concept"

    def test_placeholder_strings_read_as_absent(self):
        """Models return the literal words for a field they had nothing for. Storing "None" would
        print it to the learner as the key idea."""
        parsed = lesson_service.parse_lesson(
            {"sections": [_section(keyIdea="None", code="null", eyebrow="")]}
        )
        section = parsed["sections"][0]
        assert section["keyIdea"] is None
        assert section["code"] is None
        assert section["eyebrow"] is None

    def test_steps_that_are_not_objects_are_dropped(self):
        """A reply giving `steps: ["do this"]` would otherwise become a list of objects with no title
        and no detail, which the reader draws as empty rows."""
        parsed = lesson_service.parse_lesson(
            {
                "sections": [
                    _section(
                        steps=[
                            "just a string",
                            {"title": "Enqueue", "detail": "Add the start node."},
                            {"title": "no detail"},
                        ]
                    )
                ]
            }
        )
        assert parsed["sections"][0]["steps"] == [
            {"title": "Enqueue", "detail": "Add the start node."}
        ]

    def test_section_count_is_capped(self):
        """A model that returns a section per sentence would otherwise produce a lesson of forty
        one-line steps, and forty rows to complete."""
        parsed = lesson_service.parse_lesson(
            {"sections": [_section(title=f"S{i}") for i in range(40)]}
        )
        assert len(parsed["sections"]) == 12

    def test_a_reply_that_is_not_an_object_yields_nothing_rather_than_raising(self):
        """The caller keeps the markdown and the reader falls back to it, so a bad generation costs
        structure rather than the whole lesson."""
        for reply in (None, [], "sorry, I cannot help with that", 42):
            assert lesson_service.parse_lesson(reply)["sections"] == []


class TestKnowledgeCheck:
    def _check(self, **overrides):
        base = {
            "question": "Which traversal finds the fewest edges?",
            "explanation": "BFS explores in distance layers.",
            "choices": [
                {"id": "dfs", "label": "Depth-first", "correct": False},
                {"id": "bfs", "label": "Breadth-first", "correct": True},
            ],
        }
        return lesson_service.parse_lesson({"knowledgeCheck": {**base, **overrides}})[
            "knowledgeCheck"
        ]

    def test_a_well_formed_check_survives(self):
        check = self._check()
        assert check["question"].startswith("Which traversal")
        assert sum(1 for c in check["choices"] if c["correct"]) == 1

    def test_a_check_with_no_correct_answer_is_discarded(self):
        """The reader gates Continue on answering correctly, so an unpassable check would strand the
        learner at the end of the lesson. Dropping it lets them finish."""
        assert (
            self._check(
                choices=[
                    {"id": "a", "label": "One", "correct": False},
                    {"id": "b", "label": "Two", "correct": False},
                ]
            )
            is None
        )

    def test_a_check_with_several_correct_answers_is_discarded(self):
        """The page marks one choice right; two would make the feedback contradict itself."""
        assert (
            self._check(
                choices=[
                    {"id": "a", "label": "One", "correct": True},
                    {"id": "b", "label": "Two", "correct": True},
                ]
            )
            is None
        )

    def test_a_check_with_a_single_choice_is_discarded(self):
        assert self._check(choices=[{"id": "a", "label": "Only", "correct": True}]) is None

    def test_choices_missing_an_id_are_given_a_positional_one(self):
        """The reader keys its selection state by choice id, so two blank ids would make selecting one
        option highlight both."""
        check = self._check(
            choices=[
                {"label": "First", "correct": True},
                {"label": "Second", "correct": False},
            ]
        )
        assert [c["id"] for c in check["choices"]] == ["choice-1", "choice-2"]

    def test_a_missing_explanation_becomes_empty_rather_than_dropping_the_check(self):
        """The explanation is shown after answering; a check without one is still answerable."""
        check = self._check(explanation=None)
        assert check is not None
        assert check["explanation"] == ""


class TestObjectives:
    def test_objectives_are_trimmed_and_capped(self):
        parsed = lesson_service.parse_lesson(
            {"objectives": [f"Objective {i}" for i in range(20)] + ["", "  "]}
        )
        assert len(parsed["objectives"]) == 6

    def test_no_objectives_is_null_not_an_empty_list(self):
        """Null means none were written; the reader shows no objectives block rather than an empty
        one with a heading and nothing under it."""
        assert lesson_service.parse_lesson({"objectives": []})["objectives"] is None
        assert lesson_service.parse_lesson({})["objectives"] is None


class TestRenderMarkdown:
    def test_the_document_carries_the_same_lesson_as_the_rows(self):
        """`Topic.content` briefs Study Mode's voice tutor, so it must read as prose. Handing it the
        raw JSON reply would have the tutor reading punctuation aloud."""
        parsed = lesson_service.parse_lesson(
            {
                "objectives": ["Trace a traversal by hand"],
                "sections": [
                    _section(
                        title="Breadth-first search",
                        keyIdea="The oldest discovery leaves first.",
                        steps=[{"title": "Enqueue", "detail": "Add the start node."}],
                        bullets=["Use a queue"],
                        code="queue.push(start)",
                    )
                ],
            }
        )
        rendered = lesson_service.render_markdown("Graph traversal", parsed)

        assert rendered.startswith("# Graph traversal")
        assert "## What you'll be able to do" in rendered
        assert "- Trace a traversal by hand" in rendered
        assert "## Breadth-first search" in rendered
        assert "**Key idea:** The oldest discovery leaves first." in rendered
        assert "1. **Enqueue** — Add the start node." in rendered
        assert "```\nqueue.push(start)\n```" in rendered
        # No JSON punctuation leaked into the prose.
        assert '"paragraphs"' not in rendered

    def test_optional_blocks_are_omitted_rather_than_left_empty(self):
        rendered = lesson_service.render_markdown(
            "Study habits", lesson_service.parse_lesson({"sections": [_section()]})
        )
        assert "What you'll be able to do" not in rendered
        assert "Key idea" not in rendered
        assert "```" not in rendered
        assert "Check yourself" not in rendered
