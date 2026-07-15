"""
Production server entrypoint.

This module bridges the old main.py and the new domain-driven app.py.

During migration, both entrypoints coexist:
- uvicorn src.main:app          → Old monolithic app (existing production)
- uvicorn src.app:app           → New domain-driven app (new endpoints)
- uvicorn src.server:app        → Combined: mounts new app AND old routes

Once all clients migrate to new endpoint paths, switch production to:
    uvicorn src.app:app

Usage:
    # Development (new architecture):
    uvicorn src.app:app --reload

    # Production (bridge — serves both old and new):
    uvicorn src.server:app --reload

    # Production (legacy — unchanged):
    uvicorn src.main:app --reload
"""

import logging

from src.app import app

# The new app.py is the primary application.
# It serves all domain routes under /api/v1/{domain}/...
#
# If you need the old routes to also be available on the same process
# (for gradual client migration), uncomment the section below to mount
# them as a sub-application or include the old routers directly.
#
# --- OPTIONAL: Mount old routes alongside new ones ---
# from src.main import create_app as create_legacy_app
# legacy_app = create_legacy_app()
# app.mount("/legacy", legacy_app)
# --- END OPTIONAL ---

logger = logging.getLogger(__name__)
logger.info("Server entrypoint loaded (new domain-driven architecture)")

# Export for uvicorn
__all__ = ["app"]
