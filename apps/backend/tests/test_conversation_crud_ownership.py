from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.domains.intelligence.conversation import conversation_service
from src.shared.exceptions import NotFoundError


@pytest.mark.asyncio
async def test_conversation_read_is_owner_scoped():
    foreign = SimpleNamespace(id="session_1", user_id="other_user")
    with patch.object(
        conversation_service.intelligence_repo,
        "find_chat_session",
        AsyncMock(return_value=foreign),
    ):
        with pytest.raises(NotFoundError):
            await conversation_service.get_conversation(session_id="session_1", user_id="user_1")


@pytest.mark.asyncio
async def test_archive_checks_ownership_before_updating():
    update = AsyncMock()
    with (
        patch.object(
            conversation_service,
            "get_conversation",
            AsyncMock(return_value=SimpleNamespace(id="session_1", user_id="user_1")),
        ),
        patch.object(conversation_service.intelligence_repo, "update_chat_session", update),
    ):
        await conversation_service.archive_conversation(session_id="session_1", user_id="user_1")
    update.assert_awaited_once_with("session_1", {"isActive": False})


class _Result:
    def __init__(self, *, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = rows or []

    def scalar(self):
        return self._scalar

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)

    def all(self):
        return self._rows


class _Session:
    def __init__(self, results):
        self.results = list(results)
        self.statements = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, statement):
        self.statements.append(statement)
        return self.results.pop(0) if self.results else _Result()

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_history_checks_ownership_and_returns_the_owned_thread():
    now = datetime.now(UTC)
    rows = [
        (SimpleNamespace(id="m2", created_at=now), None, None, None, None),
        (SimpleNamespace(id="m1", created_at=now), None, None, None, None),
    ]
    session = _Session([_Result(scalar=2), _Result(rows=rows)])
    owner_check = AsyncMock()
    with (
        patch.object(conversation_service, "get_conversation", owner_check),
        patch.object(conversation_service, "get_session_factory", lambda: lambda: session),
    ):
        messages, total, has_more, cursor = await conversation_service.get_messages(
            session_id="session_1", user_id="user_1", limit=50
        )
    owner_check.assert_awaited_once_with(session_id="session_1", user_id="user_1")
    assert [row.id for row in messages] == ["m1", "m2"]
    assert (total, has_more, cursor) == (2, False, None)


@pytest.mark.asyncio
async def test_delete_checks_ownership_and_deletes_messages_with_the_session():
    session = _Session([])
    owner_check = AsyncMock()
    with (
        patch.object(conversation_service, "get_conversation", owner_check),
        patch.object(conversation_service, "get_session_factory", lambda: lambda: session),
    ):
        await conversation_service.delete_conversation(session_id="session_1", user_id="user_1")
    owner_check.assert_awaited_once_with(session_id="session_1", user_id="user_1")
    assert len(session.statements) == 2
    assert session.committed is True


@pytest.mark.asyncio
async def test_history_exposes_latest_retry_state_without_per_message_queries():
    now = datetime.now(UTC)
    user_message = SimpleNamespace(id="m1", created_at=now, role="USER")
    rows = [(user_message, "attempt_2", "FAILED", True, "PROVIDER_TIMEOUT")]
    session = _Session([_Result(scalar=1), _Result(rows=rows)])
    with (
        patch.object(conversation_service, "get_conversation", AsyncMock()),
        patch.object(conversation_service, "get_session_factory", lambda: lambda: session),
    ):
        messages, *_ = await conversation_service.get_messages(
            session_id="session_1", user_id="user_1", limit=50
        )

    assert len(session.statements) == 2
    assert messages[0].generation.latest_attempt_id == "attempt_2"
    assert messages[0].generation.status == "FAILED"
    assert messages[0].generation.retryable is True
    assert messages[0].generation.failure_code == "PROVIDER_TIMEOUT"
