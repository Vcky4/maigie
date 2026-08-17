"""The stages writing a lesson reports.

Mirrors `tests/test_quiz_generation_stages.py`, and for the same reason: the published `Literal` and the
service's tuple are two lists of the same thing, and nothing stops them drifting except a test that
compares them. A client switching on a stage name the server never sends has no honest fallback.
"""

from src.domains.knowledge import models
from src.domains.knowledge.services import lesson_service


def test_published_literal_matches_the_service():
    """The wire contract and the implementation are the same set, in the same order.

    Order matters as well as membership: the reader draws a step list from it, so a reordering would
    tick the wrong step while both lists stayed technically correct.
    """
    published = models.LessonGenerationStage.__args__  # type: ignore[attr-defined]
    assert tuple(published) == lesson_service.GenerationStage.ORDER


def test_stages_are_indexed_in_order():
    stage = lesson_service.GenerationStage
    assert stage.INDEX[stage.PREPARING] == 0
    assert stage.INDEX[stage.WRITING_LESSON] == 1
    assert stage.INDEX[stage.STRUCTURING] == 2
    assert stage.INDEX[stage.SAVING] == 3
    assert stage.INDEX[stage.READY] == 4


def test_progress_runs_from_zero_to_one():
    stage = lesson_service.GenerationStage
    assert lesson_service.generation_progress(stage.PREPARING) == 0.0
    assert lesson_service.generation_progress(stage.READY) == 1.0


def test_progress_increases_monotonically():
    values = [
        lesson_service.generation_progress(stage) for stage in lesson_service.GenerationStage.ORDER
    ]
    assert values == sorted(values)
    assert len(set(values)) == len(values)


def test_progress_is_none_for_an_unknown_or_absent_stage():
    """None rather than 0.

    A topic whose lesson was written before the column existed recorded no stage, and reporting 0 would
    claim it never started when in fact it finished.
    """
    assert lesson_service.generation_progress(None) is None
    assert lesson_service.generation_progress("WRITING_QUESTIONS") is None
    assert lesson_service.generation_progress("") is None
