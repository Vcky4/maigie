"""Opens one entitlement memo scope per HTTP request.

`entitlement_service.resolve` is the single answer to "is this learner Plus right now"
(MAIGIE_PLUS_COMMERCIAL_PLAN.md Decision B), which means a request that asks it twice pays for the
join twice. `feature_tier_service.check_capability` and `feature_flags.get_quality_tier` do exactly
that, so the ask path resolves at least twice per turn.

The memo lives in a `contextvars.ContextVar` that only holds a dict while a scope is open, and this
is the only thing in the application that opens one.

**Pure ASGI on purpose, not `BaseHTTPMiddleware`.** `BaseHTTPMiddleware` runs `dispatch` in a task of
its own and calls the downstream app from another; context variables set there propagate downwards
only because the child task copies the context at creation, and nothing propagates back up. A plain
ASGI callable runs in the request's own task, so the scope wraps the endpoint with no caveat to
remember.

**`http` only.** Websocket connections deliberately get no memo. A `study_voice` relay holds one
scope open for the length of a tutoring session and bills every couple of seconds; caching the
entitlement there would mean a pass could expire mid-session and go on being honoured until the
learner hung up. Long-lived metered connections need the fresh read.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

from src.domains.billing.services import entitlement_service


class EntitlementScopeMiddleware:
    """Wrap each HTTP request in `entitlement_service.request_scope()`."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        with entitlement_service.request_scope():
            await self.app(scope, receive, send)
