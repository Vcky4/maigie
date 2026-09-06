"""The optional LLM digest copy is off by default, shadow by default, and always falls back.

These pin the four rules the Phase 6 LLM layer must obey structurally, not by trust: disabled ->
deterministic and no model call; enabled+shadow -> a valid proposal is recorded but the learner
still gets the deterministic copy; enabled+live -> a valid, safe proposal is sent; and every failure
mode (provider raise, wrong shape, over-long, a link, markup) -> the exact deterministic copy, never
a raise.
"""

from __future__ import annotations

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

import pytest  # noqa: E402

from src.config import Settings  # noqa: E402
from src.domains.notifications import digest_copy  # noqa: E402

USER = "learner-1"
DET_TITLE = "Your learning this week"
DET_BODY = "• Reviewed 12 cards\n• Finished Chapter 3"
ITEMS = [("Reviewed 12 cards", ""), ("Finished Chapter 3", "Great pace")]


def _settings(**overrides) -> Settings:
    base = {
        "NOTIFICATION_DIGEST_LLM_ENABLED": True,
        "NOTIFICATION_DIGEST_LLM_ALLOWLIST": [USER],
        "NOTIFICATION_DIGEST_LLM_SHADOW_ONLY": True,
    }
    base.update(overrides)
    return Settings(**base)


def _patch_llm(monkeypatch, result):
    """Replace the resilient JSON generator the module imports locally.

    `result` may be a value to return or an Exception subclass/instance to raise, mirroring a
    provider outage or a budget refusal.
    """

    async def _fake(*_args, **_kwargs):
        if isinstance(result, Exception):
            raise result
        if isinstance(result, type) and issubclass(result, Exception):
            raise result("boom")
        return result

    monkeypatch.setattr(
        "src.domains.personal_learning.services.llm_resilient.generate_content_json",
        _fake,
    )


async def _resolve(settings) -> digest_copy.DigestCopyOutcome:
    return await digest_copy.resolve_digest_copy(
        user_id=USER,
        settings_category="LEARNING",
        period="WEEKLY",
        items=ITEMS,
        deterministic_title=DET_TITLE,
        deterministic_body=DET_BODY,
        settings=settings,
    )


@pytest.mark.asyncio
async def test_disabled_returns_deterministic_and_calls_no_model(monkeypatch) -> None:
    called = False

    async def _fake(*_a, **_k):
        nonlocal called
        called = True
        return {"title": "x", "body": "y"}

    monkeypatch.setattr(
        "src.domains.personal_learning.services.llm_resilient.generate_content_json", _fake
    )

    outcome = await _resolve(_settings(NOTIFICATION_DIGEST_LLM_ENABLED=False))

    assert outcome.title == DET_TITLE and outcome.body == DET_BODY
    assert outcome.status == digest_copy.STATUS_OFF
    assert outcome.proposed is False
    assert called is False, "a disabled capability must never call a model"


@pytest.mark.asyncio
async def test_shadow_records_proposal_but_learner_gets_deterministic(monkeypatch) -> None:
    _patch_llm(
        monkeypatch, {"title": "A strong week", "body": "You reviewed and finished a chapter."}
    )

    outcome = await _resolve(_settings(NOTIFICATION_DIGEST_LLM_SHADOW_ONLY=True))

    # The learner is unaffected — this is the whole safety guarantee of shadow mode.
    assert outcome.title == DET_TITLE and outcome.body == DET_BODY
    assert outcome.status == digest_copy.STATUS_SHADOW
    assert outcome.proposed is True


@pytest.mark.asyncio
async def test_live_applies_a_valid_proposal(monkeypatch) -> None:
    _patch_llm(
        monkeypatch, {"title": "A strong week", "body": "You reviewed and finished a chapter."}
    )

    outcome = await _resolve(_settings(NOTIFICATION_DIGEST_LLM_SHADOW_ONLY=False))

    assert outcome.title == "A strong week"
    assert outcome.body == "You reviewed and finished a chapter."
    assert outcome.status == digest_copy.STATUS_APPLIED
    assert outcome.proposed is True


@pytest.mark.asyncio
async def test_live_falls_back_when_the_model_raises(monkeypatch) -> None:
    _patch_llm(monkeypatch, RuntimeError)

    outcome = await _resolve(_settings(NOTIFICATION_DIGEST_LLM_SHADOW_ONLY=False))

    assert outcome.title == DET_TITLE and outcome.body == DET_BODY
    assert outcome.status == digest_copy.STATUS_FALLBACK
    assert outcome.proposed is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        {"title": "ok"},  # missing body
        {"title": "", "body": "nonempty"},  # empty title
        {"title": "ok", "body": "x" * 601},  # body over the length bound
        {"title": "ok", "body": "See https://spam.example for more"},  # a link
        {"title": "ok", "body": "Great work <b>this</b> week"},  # markup
        "not a dict",  # wrong top-level shape
    ],
)
async def test_live_falls_back_on_unusable_output(monkeypatch, bad) -> None:
    _patch_llm(monkeypatch, bad)

    outcome = await _resolve(_settings(NOTIFICATION_DIGEST_LLM_SHADOW_ONLY=False))

    assert outcome.title == DET_TITLE and outcome.body == DET_BODY
    assert outcome.status == digest_copy.STATUS_FALLBACK
    assert outcome.proposed is False


def test_sanitise_collapses_whitespace_and_keeps_plain_text() -> None:
    assert digest_copy._sanitise_plain_text("  You   studied\n  hard  ") == "You studied\nhard"


@pytest.mark.parametrize(
    "value",
    ["visit http://x.io", "www.spam.net", "has <i>markup</i>", "bell\x07char"],
)
def test_sanitise_refuses_links_markup_and_control_chars(value) -> None:
    assert digest_copy._sanitise_plain_text(value) is None
