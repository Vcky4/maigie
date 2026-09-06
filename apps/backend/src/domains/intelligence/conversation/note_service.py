"""Note service facade — re-exports from personal_learning domain."""

from src.domains.personal_learning.services.note_impl import latest_note_for_topic

__all__ = ["latest_note_for_topic"]
