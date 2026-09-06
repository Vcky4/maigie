"""Turn an unhandled exception into a real response, inside the CORS middleware.

`app.add_exception_handler(Exception, ...)` looks like it covers this, and it does produce the right JSON —
but Starlette routes the catch-all `Exception` handler through `ServerErrorMiddleware`, which sits **outside
every middleware the application adds**, including CORS. So the 500 it returns never passes back through
`CORSMiddleware` and carries no `Access-Control-Allow-Origin` header.

The consequence is that a browser cannot see the error at all. It reports:

    Access to XMLHttpRequest at '.../generate' from origin 'http://localhost:4200' has been blocked by
    CORS policy: No 'Access-Control-Allow-Origin' header is present on the requested resource.

...for what is actually a `500` with a stack trace in the server log. That is worse than an unhelpful error
message: it points at the wrong subsystem. Time gets spent on CORS configuration, which is correct, while
the real failure — in this case a truncated model reply raising `JSONDecodeError` — goes unexamined. It also
means the client's own error handling never runs, because `axios` sees a network failure rather than a status
code, so a page cannot distinguish "the server broke" from "the server is unreachable".

This middleware is registered **first**, which makes it innermost, so it wraps the router more closely than
the logging, session and CORS layers. An exception escaping a route is converted here, and the response then
travels back out through CORS like any other, collecting its headers.

`ServerErrorMiddleware` stays where it is as the last line of defence — if this middleware itself fails,
something still answers.
"""

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


class UnhandledExceptionMiddleware(BaseHTTPMiddleware):
    """Convert an escaping exception into a `500` that CORS can still annotate."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        try:
            return await call_next(request)
        except Exception as exc:
            # Logged with the traceback here rather than left to `ServerErrorMiddleware`, which will not see
            # this exception now that it is handled. Sentry reporting stays in the shared handler, which this
            # deliberately does not duplicate — one report per failure.
            logger.error(
                "Unhandled exception in %s %s: %s: %s",
                request.method,
                request.url.path,
                type(exc).__name__,
                exc,
                exc_info=True,
            )

            try:
                import sentry_sdk

                sentry_sdk.capture_exception(exc)
            except Exception:  # pragma: no cover - reporting must never mask the original failure
                logger.debug("Could not report the exception to Sentry", exc_info=True)

            # The same body `unhandled_exception_handler` returns, so a client sees one shape for a server
            # error however it was produced. The message is deliberately generic: an exception string can
            # carry a query, a path or a key.
            return JSONResponse(
                status_code=500,
                content={
                    "status_code": 500,
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "An internal server error occurred. Please try again later.",
                },
            )
