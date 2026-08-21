"""The plan gate on document generation, and when it runs.

Both routes accept a `format` and a `style` that only PLUS includes. The check for them lived
inside ``create_from_prompt``, which the async route reaches only from the worker — so a FREE
learner asking for Word got a `202`, waited out a thirty-to-sixty-second generation, and was then
handed the failure as ``str(HTTPException)``: the string ``"403: {'upgradeRequired': True, ...}"``.
Nothing in that is actionable by a client, and a client showing it verbatim is the honest option.

These run without a database and without Celery. What they pin is *ordering* — the gate before the
queue — which is invisible in any response body, and which is exactly the property that was wrong.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.domains.personal_learning.services import document_impl
from src.domains.personal_learning.services.feature_tier_service import (
    CapabilityAllowed,
    CapabilityDenied,
)

USER = "doc-capability-user"


def _deny(monkeypatch, *, expect: str | None = None) -> list[str]:
    """Refuse every capability check, recording what was asked about."""
    asked: list[str] = []

    async def _check(user_id, capability, *, requested_value=None):
        asked.append(requested_value)
        return CapabilityDenied(
            reason="Word export is on Maigie Plus.",
            capability=capability,
            upgrade_url="/subscription",
            trial_available=True,
            upgrade_value="Word and slide exports, and every writing style.",
        )

    monkeypatch.setattr(
        "src.domains.personal_learning.services.feature_tier_service.check_capability", _check
    )
    return asked


def _allow(monkeypatch) -> list[str]:
    asked: list[str] = []

    async def _check(user_id, capability, *, requested_value=None):
        asked.append(requested_value)
        return CapabilityAllowed(tier="plus")

    monkeypatch.setattr(
        "src.domains.personal_learning.services.feature_tier_service.check_capability", _check
    )
    return asked


@pytest.mark.asyncio
async def test_the_free_defaults_are_not_asked_about(monkeypatch):
    """PDF and academic are what FREE includes, so checking them is a round trip to be told so."""
    asked = _deny(monkeypatch)

    await document_impl.ensure_document_capabilities(user_id=USER, format="pdf", style="academic")

    assert asked == []


@pytest.mark.asyncio
async def test_a_gated_format_raises_the_typed_upgrade_payload(monkeypatch):
    _deny(monkeypatch)

    with pytest.raises(HTTPException) as raised:
        await document_impl.ensure_document_capabilities(
            user_id=USER, format="docx", style="academic"
        )

    assert raised.value.status_code == 403
    # The shape the clients branch on. A bare string here is what the async path used to deliver.
    assert raised.value.detail["upgradeRequired"] is True
    assert raised.value.detail["reason"] == "Word export is on Maigie Plus."
    assert raised.value.detail["trialAvailable"] is True
    assert raised.value.detail["upgradeUrl"] == "/subscription"
    assert raised.value.detail["upgradeValue"]


@pytest.mark.asyncio
async def test_a_gated_style_is_refused_even_when_the_format_is_free(monkeypatch):
    """The style gate is the one a learner trips without choosing anything paid-looking.

    Both clients derive `style` from the document *type* — a report becomes `report`, a
    presentation becomes `minimal` — so picking "Report" requests a PLUS style while the format
    stays PDF. Checking only the format would let that through to the worker.
    """
    asked = _deny(monkeypatch)

    with pytest.raises(HTTPException) as raised:
        await document_impl.ensure_document_capabilities(user_id=USER, format="pdf", style="report")

    assert raised.value.status_code == 403
    assert asked == ["report"]


@pytest.mark.asyncio
async def test_both_departures_are_checked_when_both_are_requested(monkeypatch):
    asked = _allow(monkeypatch)

    await document_impl.ensure_document_capabilities(user_id=USER, format="docx", style="report")

    assert asked == ["docx", "report"]


@pytest.mark.asyncio
async def test_the_async_route_refuses_before_it_queues_anything(monkeypatch):
    """The point of the change: no task id, no cache write, no job.

    Asserted by making both of those explode. A gate that runs after `apply_async` would reach
    them, and a gate that runs after the cache write would leave an owner record for a job nobody
    queued.
    """
    from src.domains.personal_learning import models, routes

    _deny(monkeypatch)

    async def _explode_cache_set(*args, **kwargs):
        raise AssertionError("The job owner was recorded for a request that must be refused")

    def _explode_apply(*args, **kwargs):
        raise AssertionError("A job was queued for a request that must be refused")

    monkeypatch.setattr("src.shared.infrastructure.cache.set", _explode_cache_set)
    monkeypatch.setattr(
        "src.workers.personal_learning_tasks.generate_document_task.apply_async", _explode_apply
    )

    body = models.DocumentGenerateRequest(
        type="essay", title="A short essay", prompt="Cover the basics", format="docx"
    )

    with pytest.raises(HTTPException) as raised:
        await routes.generate_document_async(body, SimpleNamespace(id=USER))

    assert raised.value.status_code == 403
    assert raised.value.detail["upgradeRequired"] is True


@pytest.mark.asyncio
async def test_an_unsupported_format_is_still_refused_first(monkeypatch):
    """Format normalization runs before the plan gate, and should stay there.

    `epub` is not a product format at any tier, so answering "upgrade for this" would be false.
    """
    from src.domains.personal_learning import models, routes

    asked = _deny(monkeypatch)

    body = models.DocumentGenerateRequest(
        type="essay", title="A short essay", prompt="Cover the basics", format="epub"
    )

    with pytest.raises(HTTPException) as raised:
        await routes.generate_document_async(body, SimpleNamespace(id=USER))

    assert raised.value.status_code == 400
    assert asked == []
