"""Research domain — educator market-validation questionnaire.

Two surfaces with deliberately different halves: an unauthenticated respondent API authorised by a
per-response resume token, and a staff review API. They are separate routers so the mount points in
`app.py` say which is which.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from .admin_routes import router as admin_router
from .routes import router

__all__ = ["admin_router", "router"]
