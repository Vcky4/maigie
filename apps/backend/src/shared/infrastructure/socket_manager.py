"""Websocket connection registry.

This module used to define a second ``ConnectionManager`` whose methods were stubs: it
built a ``_connections`` dict, never added anything to it, and had ``send_json``,
``send_to_user`` and ``disconnect`` as ``pass``. Every message the chat handler sent was
discarded, and ``disconnect(connection_id)`` would have raised ``TypeError`` because the
stub required a second argument the caller does not pass.

A working manager already exists in ``src.core.websocket``, with connection tracking,
per-user fan-out, channel subscriptions, heartbeats and stale-connection cleanup. The
course-generation service was already using it. So the fix is not to write a second
implementation but to stop having one: this module re-exports the same instance.

Sharing the instance is the part that matters. Two managers mean two registries, so a
connection accepted through one is invisible to the other, which is precisely why chat
messages went nowhere while course progress updates arrived.

Single-process only: the registry is in memory, so with more than one worker a message
reaches only the worker holding that user's socket. Fanning out across workers needs a
shared broker and is not addressed here.
"""

from src.core.websocket import ConnectionManager, manager

__all__ = ["ConnectionManager", "manager"]
