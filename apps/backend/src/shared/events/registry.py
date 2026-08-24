"""Import the modules that hold `@listen` handlers, so the bus actually has them.

`@listen` registers a handler as a **side effect of importing the module it is written in** — the
decorator appends to `_handlers` at decoration time and nothing else does. So a handler exists only
if something imported its module, and until this file, nothing did:

    >>> import src.app
    >>> from src.shared.events.bus import _handlers
    >>> dict(_handlers)
    {}

Ten handlers across two modules, none of them reachable in a process that had only imported the
application. The two importers that exist do it *function-locally* inside a request path
(`personal_learning/routes.py` imports `.events` for an emitter), which is worse than not importing
at all: whether a handler runs depends on whether some unrelated code path happened to run first in
that same process. A fresh web worker dispatched nothing; a warm one dispatched five handlers; a
Celery worker had its own separate answer. And `emit` returns silently when nothing is listening:

    handlers = _handlers.get(event_name, [])
    if not handlers:
        return

so there was no signal either way, at any log level above debug.

This is the same shape as the block immediately above the beat schedule in `core/celery_app.py`
("Ensure feature tasks are imported so Celery registers them"). Registration by import needs one
explicit place that does the importing, or it does not happen.

**Every module holding a `@listen` must be listed here.** `tests/test_event_bus.py` scans the source
tree for `@listen` and fails if this tuple has drifted, so a handler added later cannot be quietly
unreachable the way these ten were.
"""

from __future__ import annotations

import importlib
import logging

logger = logging.getLogger(__name__)

#: Modules whose import registers domain event handlers. Order is irrelevant — registration is
#: append-only per event name, and no handler depends on another.
HANDLER_MODULES: tuple[str, ...] = (
    "src.domains.personal_learning.events",
    "src.domains.intelligence.observation.tracker",
    "src.domains.progress.listeners",
)


def register_handlers() -> int:
    """Import every handler module. Returns the number of handlers now registered.

    Idempotent, because module import is: calling it twice does not double-register, since the second
    `import_module` is a `sys.modules` hit and the decorators do not run again. That matters because
    the web app calls it from `lifespan` and the Celery app calls it at module import, and a process
    can be both.

    A failure to import one module is logged and does not stop the others. A missing handler is a lost
    notification; refusing to start the application over it would be a worse trade.
    """
    from .bus import get_handler_count

    for module_name in HANDLER_MODULES:
        try:
            importlib.import_module(module_name)
        except Exception:
            logger.exception("Event handler module failed to import: %s", module_name)

    count = get_handler_count()
    logger.info("Domain event handlers registered: %d", count)
    return count
