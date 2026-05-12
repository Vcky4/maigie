"""Streaming / WebSocket disconnect detection for LLM callbacks (provider-agnostic)."""

from __future__ import annotations


class StreamConsumerDisconnected(Exception):
    """WebSocket client left while streaming; not an LLM failure."""

    __slots__ = ("partial_turn_text",)

    def __init__(self, partial_turn_text: str = "") -> None:
        self.partial_turn_text = partial_turn_text
        super().__init__("client disconnected during stream")


def _stream_disconnect_exception_types() -> tuple[type[BaseException], ...]:
    types_list: list[type[BaseException]] = []
    try:
        import starlette.websockets as starlette_ws

        types_list.append(starlette_ws.WebSocketDisconnect)
    except Exception:
        pass
    try:
        from uvicorn.protocols.utils import ClientDisconnected as UvicornClientDisconnected

        types_list.append(UvicornClientDisconnected)
    except Exception:
        pass
    try:
        import uvicorn.protocols.websockets.websockets_impl as uvicorn_ws_impl

        _cd = getattr(uvicorn_ws_impl, "ClientDisconnected", None)
        if isinstance(_cd, type) and issubclass(_cd, BaseException) and _cd not in types_list:
            types_list.append(_cd)
    except Exception:
        pass
    try:
        import websockets.exceptions as websockets_exc

        for _name in ("ConnectionClosed", "ConnectionClosedError", "ConnectionClosedOK"):
            _t = getattr(websockets_exc, _name, None)
            if isinstance(_t, type) and issubclass(_t, BaseException):
                types_list.append(_t)
    except Exception:
        pass
    return tuple(types_list)


_STREAM_DISCONNECT_TYPES = _stream_disconnect_exception_types()


def is_websocket_consumer_disconnect(exc: BaseException) -> bool:
    """True when the failure is only because the HTTP/WebSocket client went away."""
    visited: set[int] = set()

    def walk(err: BaseException | None) -> bool:
        if err is None or id(err) in visited:
            return False
        visited.add(id(err))
        if isinstance(err, _STREAM_DISCONNECT_TYPES):
            return True
        if isinstance(err, RuntimeError):
            _m = str(err).lower()
            if "close message has been sent" in _m or "cannot call" in _m and "send" in _m:
                return True
        if isinstance(err, BaseExceptionGroup):
            return any(walk(x) for x in err.exceptions)
        if walk(err.__cause__):
            return True
        ctx = err.__context__
        if ctx is not err.__cause__ and walk(ctx):
            return True
        return False

    return walk(exc)
