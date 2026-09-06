"""
Reasoning service — LLM orchestration for chat responses.

**Both functions this module used to export were unreachable, and are deleted.**

`generate_response` opened with the comment "During migration, delegate to existing chat service" and
then imported `reasoning.chat_impl.process_chat_message`. That function has never existed:
`chat_impl.py` defines `merge_generic_sessions` and `get_or_create_onboarding_session` and nothing
else, and the only two references to `process_chat_message` in the repository were this import and the
call beneath it. Its one caller, `POST /api/v1/intelligence/chat`, is deleted with it.

`generate_streaming_response` raised `NotImplementedError` with a docstring promising to yield chunks.
Streaming is the WebSocket handler's job and always was.

The module is kept as a placeholder rather than removed because the pipeline extracted out of
`conversation/websocket_handler.py` lands next door as `conversation/ask_service.py`, and leaving this
file empty-but-documented is how the next person finds out that the obvious-looking home for that code
was tried, was never wired up, and should not be revived. Delete it once `ask_service` exists.
"""

import logging

logger = logging.getLogger(__name__)
