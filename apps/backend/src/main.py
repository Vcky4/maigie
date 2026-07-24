"""
Application entrypoint — delegates to the new domain-driven app.

This file exists for backward compatibility with deployment configs
that reference `src.main:app`. It re-exports the app from `src.app`.

The actual application factory is in src/app.py.

Usage:
    uvicorn src.main:app --reload        # Works (backward compat)
    uvicorn src.app:app --reload         # Also works (canonical)
    uvicorn src.server:app --reload      # Also works (bridge)
"""

from src.app import app  # noqa: F401

__all__ = ["app"]
