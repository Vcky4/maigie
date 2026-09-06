"""An unhandled exception must reach the browser as a `500`, with CORS headers.

Guards a defect that made a real failure much harder to find. `app.add_exception_handler(Exception, ...)` is
registered and produces the right body, but Starlette routes the catch-all through `ServerErrorMiddleware`,
which sits **outside every middleware the app adds** — including CORS. So the `500` carried no
`Access-Control-Allow-Origin`, and the browser reported:

    Access to XMLHttpRequest at '.../generate' ... has been blocked by CORS policy: No
    'Access-Control-Allow-Origin' header is present on the requested resource.

...for what was actually a `JSONDecodeError` from a truncated model reply. The error pointed at CORS, which
was configured correctly, while the real cause sat in the server log. It also meant the client saw a network
failure rather than a status code, so no page could tell "the server broke" from "the server is unreachable".
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from src.shared.middleware import UnhandledExceptionMiddleware

ORIGIN = "http://localhost:4200"


def _app_with(*, protected: bool) -> FastAPI:
    """A minimal app with CORS, optionally with the conversion middleware inside it.

    Built here rather than imported from `src.app` so the test isolates the middleware interaction: the real
    app carries sessions, logging and security headers, none of which bear on whether a `500` keeps its CORS
    header.
    """
    app = FastAPI()

    # Added first, so innermost — inside CORS, which is what makes the difference.
    if protected:
        app.add_middleware(UnhandledExceptionMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[ORIGIN],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.post("/boom")
    async def boom():
        # A JSONDecodeError is the shape that caused this, but any escaping exception behaves the same.
        raise ValueError("something the route did not expect")

    @app.get("/fine")
    async def fine():
        return {"ok": True}

    return app


class TestUnhandledExceptionKeepsCorsHeaders:
    def test_a_500_carries_the_cors_header(self):
        client = TestClient(_app_with(protected=True), raise_server_exceptions=False)
        response = client.post("/boom", headers={"Origin": ORIGIN})

        assert response.status_code == 500
        # The assertion that matters. Without this header the browser discards the response and reports a
        # CORS failure instead of a server error.
        assert response.headers.get("access-control-allow-origin") == ORIGIN

    def test_the_body_is_the_shape_clients_already_handle(self):
        client = TestClient(_app_with(protected=True), raise_server_exceptions=False)
        payload = client.post("/boom", headers={"Origin": ORIGIN}).json()

        assert payload["status_code"] == 500
        assert payload["code"] == "INTERNAL_SERVER_ERROR"
        # Generic on purpose: an exception string can carry a query, a path or a key.
        assert "something the route did not expect" not in payload["message"]

    def test_without_the_middleware_the_header_is_missing(self):
        """The control. If this ever starts passing, `ServerErrorMiddleware` has changed behaviour and the
        middleware may no longer be needed — but until then this is what the bug looked like."""
        client = TestClient(_app_with(protected=False), raise_server_exceptions=False)
        response = client.post("/boom", headers={"Origin": ORIGIN})

        assert response.status_code == 500
        assert response.headers.get("access-control-allow-origin") is None

    def test_successful_responses_are_untouched(self):
        client = TestClient(_app_with(protected=True))
        response = client.get("/fine", headers={"Origin": ORIGIN})

        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert response.headers.get("access-control-allow-origin") == ORIGIN


class TestRealAppRegistersItInsideCors:
    """The middleware only helps if it is inside CORS, which depends on registration order."""

    def test_the_conversion_middleware_is_inside_the_cors_middleware(self):
        from src.app import create_app

        app = create_app()
        classes = [middleware.cls.__name__ for middleware in app.user_middleware]

        assert "UnhandledExceptionMiddleware" in classes, "the middleware is not registered at all"
        assert "CORSMiddleware" in classes

        # `user_middleware` is outermost-first, so CORS must appear before the converter for the converter to
        # be inside it.
        assert classes.index("CORSMiddleware") < classes.index("UnhandledExceptionMiddleware"), (
            f"UnhandledExceptionMiddleware must be registered before CORSMiddleware so it sits inside it; "
            f"order is {classes}"
        )


class TestJsonRepair:
    """The failure that exposed all of the above: a model reply cut off mid-string.

    The old repair made truncation worse — it trimmed to the last `}` or `]`, which for a truncated payload
    cuts at whatever nested object happened to close last and yields a fragment that is still invalid.
    """

    def test_a_reply_truncated_mid_string_is_recovered(self):
        from src.domains.personal_learning.services.llm_resilient import _repair_json

        # The reported shape: a list of sections where the last one is incomplete.
        truncated = (
            '{"sections": [{"title": "One", "paragraphs": ["done"]}, '
            '{"title": "Two", "paragraphs": ["cut off here'
        )
        recovered = _repair_json(truncated)

        assert recovered is not None
        assert [section["title"] for section in recovered["sections"]] == ["One", "Two"]

    def test_deeply_nested_truncation_is_closed_in_the_right_order(self):
        from src.domains.personal_learning.services.llm_resilient import _repair_json

        recovered = _repair_json('{"a": {"b": {"c": [1, 2, {"d": "unterminated')
        assert recovered == {"a": {"b": {"c": [1, 2, {"d": "unterminated"}]}}}

    def test_a_reply_wrapped_in_prose_still_works(self):
        """The case the original repair was written for, which must keep working."""
        from src.domains.personal_learning.services.llm_resilient import _repair_json

        assert _repair_json('Here you go: {"a": 1} hope that helps') == {"a": 1}

    def test_a_trailing_comma_falls_back_to_the_last_complete_entry(self):
        from src.domains.personal_learning.services.llm_resilient import _repair_json

        assert _repair_json('{"a": 1, "b": [1,2],') == {"a": 1, "b": [1, 2]}

    def test_a_reply_with_no_json_at_all_returns_none(self):
        """`None` rather than an exception, so the caller chooses between a fallback and an error. Repair is
        best-effort and failing at it is not exceptional."""
        from src.domains.personal_learning.services.llm_resilient import _repair_json

        assert _repair_json("I'm sorry, I can't help with that.") is None
        assert _repair_json("") is None


class TestFallbackSemantics:
    """`fallback=None` means "no fallback — raise", not "return None".

    Both new generation routes passed `None` meaning the second, which is how a truncated reply became an
    unhandled `500`. The ambiguity is documented on the helper now; this pins the behaviour so a later change
    to the default cannot silently alter what every existing caller gets.
    """

    @pytest.mark.asyncio
    async def test_a_supplied_fallback_is_returned_on_bad_json(self, monkeypatch):
        from src.domains.personal_learning.services import llm_resilient

        async def unusable(*args, **kwargs):
            return "not json at all"

        monkeypatch.setattr(llm_resilient, "generate_content", unusable)
        assert await llm_resilient.generate_content_json("p", fallback={}) == {}

    @pytest.mark.asyncio
    async def test_no_fallback_raises(self, monkeypatch):
        from src.domains.personal_learning.services import llm_resilient

        async def unusable(*args, **kwargs):
            return "not json at all"

        monkeypatch.setattr(llm_resilient, "generate_content", unusable)
        with pytest.raises(Exception):
            await llm_resilient.generate_content_json("p")
