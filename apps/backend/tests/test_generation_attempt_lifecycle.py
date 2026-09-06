import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.domains.intelligence.conversation import ask_service
from src.domains.intelligence.reasoning.llm.context import mark_tool_side_effect_intent
from src.domains.intelligence.repository import (
    AttemptFenceLostError,
    IntelligenceRepository,
)


@pytest.mark.asyncio
async def test_live_attempt_heartbeats_until_work_finishes():
    renew_permits: asyncio.Queue[None] = asyncio.Queue()
    renewed = asyncio.Event()
    calls = 0

    async def heartbeat(_attempt_id):
        nonlocal calls
        calls += 1
        if calls == 2:
            renewed.set()

    async def controlled_sleep(_seconds):
        await renew_permits.get()

    with patch.object(ask_service.asyncio, "sleep", controlled_sleep):
        async with ask_service.maintain_attempt_lease("attempt_1", heartbeat, interval_seconds=60):
            assert calls == 1, "lease ownership is established before work starts"
            renew_permits.put_nowait(None)
            await asyncio.wait_for(renewed.wait(), timeout=1)
            assert calls == 2

    assert calls == 2, "renewal stops when the work context exits"


@pytest.mark.asyncio
async def test_lost_heartbeat_fence_interrupts_the_old_worker():
    calls = 0

    async def heartbeat(_attempt_id):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise AttemptFenceLostError("attempt_1")

    with pytest.raises(AttemptFenceLostError):
        async with ask_service.maintain_attempt_lease(
            "attempt_1", heartbeat, interval_seconds=0.01
        ):
            await asyncio.sleep(1)


class _WriteSession:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount
        self.added = []
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        if self.added and self.added[0].id is None:
            self.added[0].id = "assistant_1"

    async def execute(self, _statement):
        return SimpleNamespace(rowcount=self.rowcount)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def refresh(self, _row):
        return None


def _assistant_data():
    return {
        "sessionId": "session_1",
        "userId": "user_1",
        "role": "ASSISTANT",
        "content": "Answer",
        "tokenCount": 2,
        "askMode": "HTTP",
        "replyToMessageId": "user_message_1",
    }


@pytest.mark.asyncio
async def test_atomic_completion_commits_assistant_and_success_together():
    repo = IntelligenceRepository()
    session = _WriteSession(rowcount=1)
    with patch.object(repo, "_session", AsyncMock(return_value=session)):
        message = await repo.complete_attempt("attempt_1", _assistant_data())

    assert message.id == "assistant_1"
    assert session.committed is True
    assert session.rolled_back is False


@pytest.mark.asyncio
async def test_fenced_completion_rolls_back_assistant_insertion():
    repo = IntelligenceRepository()
    session = _WriteSession(rowcount=0)
    with patch.object(repo, "_session", AsyncMock(return_value=session)):
        with pytest.raises(AttemptFenceLostError):
            await repo.complete_attempt("stale_attempt", _assistant_data())

    assert session.rolled_back is True
    assert session.committed is False


@pytest.mark.asyncio
async def test_mutating_tool_intent_is_marked_before_execution_and_fails_closed():
    order = []

    async def marker(*_args, **_kwargs):
        order.append("marked")

    await mark_tool_side_effect_intent(["get_user_notes", "create_note"], marker)
    order.append("handler")
    assert order == ["marked", "handler"]

    async def failed_marker(*_args, **_kwargs):
        raise RuntimeError("marker unavailable")

    with pytest.raises(RuntimeError, match="marker unavailable"):
        await mark_tool_side_effect_intent(["delete_course"], failed_marker)


@pytest.mark.asyncio
async def test_stop_after_ack_before_generation_registration_is_preserved():
    attempt_id = "attempt_pending_stop"
    ask_service.prepare_cancellation(attempt_id)
    assert ask_service.cancel_turn(attempt_id) is True

    generate = AsyncMock(
        return_value=(
            "should not complete",
            {"input_tokens": 1, "output_tokens": 1},
            [],
            [],
        )
    )
    effects = SimpleNamespace(generate=generate)
    holder = {}
    with pytest.raises(asyncio.CancelledError):
        async with ask_service.cancellable_turn(attempt_id, holder):
            task = asyncio.create_task(effects.generate())
            holder["task"] = task
            if ask_service.cancellation_requested(attempt_id):
                task.cancel()
            await task

    assert ask_service.cancellation_requested(attempt_id) is False
    assert attempt_id not in ask_service.cancellable_turns()
