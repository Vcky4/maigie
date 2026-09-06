"""Tests for the space membership dependency.

`require_space_membership` carried a `TODO` and returned unconditionally, so a dependency
whose name promises enforcement enforced nothing. It is exported from `shared.auth`
alongside the working guards, which made it look ready to use: any endpoint adopting it
would have been silently unprotected. Nothing had adopted it, so no endpoint was exposed,
but that is luck rather than design.

These tests cover the three outcomes and, importantly, that a non-member cannot tell a
space they are excluded from apart from one that does not exist.
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from src.shared.auth.dependencies import require_space_membership  # noqa: E402


class FakeSession:
    """Returns a fixed membership row (or none) for the single SELECT under test."""

    def __init__(self, row):
        self._row = row
        self.executed = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _stmt):
        self.executed += 1
        row = self._row

        class Result:
            def first(self):
                return row

        return Result()


@pytest.fixture
def user():
    return SimpleNamespace(id="u-1", email="a@b.c")


def _patch_session(monkeypatch, row):
    session = FakeSession(row)
    # `dependencies` imports get_session_factory at module level, so it holds a direct
    # reference; patching the source module would have no effect here.
    monkeypatch.setattr(
        "src.shared.auth.dependencies.get_session_factory", lambda: (lambda: session)
    )
    return session


async def test_personal_context_passes_through_without_querying(monkeypatch, user):
    """No spaceId means a personal request; it must not cost a query."""
    session = _patch_session(monkeypatch, ("m-1",))

    result = await require_space_membership(current_user=user, space_id=None)

    assert result is user
    assert session.executed == 0


async def test_a_member_is_allowed(monkeypatch, user):
    session = _patch_session(monkeypatch, ("m-1",))

    result = await require_space_membership(current_user=user, space_id="sp-1")

    assert result is user
    assert session.executed == 1


async def test_a_non_member_is_rejected(monkeypatch, user):
    """The regression that mattered: this previously returned the user regardless."""
    _patch_session(monkeypatch, None)

    with pytest.raises(HTTPException) as excinfo:
        await require_space_membership(current_user=user, space_id="sp-1")

    assert excinfo.value.status_code == 404


async def test_a_non_member_cannot_distinguish_forbidden_from_missing(monkeypatch, user):
    """404 rather than 403.

    Whether a given space exists is not something a non-member should be able to probe by
    comparing status codes.
    """
    _patch_session(monkeypatch, None)

    with pytest.raises(HTTPException) as excinfo:
        await require_space_membership(current_user=user, space_id="sp-does-not-exist")

    assert excinfo.value.status_code == 404
    assert excinfo.value.detail["code"] == "SPACE_NOT_FOUND"
    # Must not leak that the space is real but closed to this user.
    assert "member" not in str(excinfo.value.detail).lower()
