"""Canonical notification HTTP and realtime API."""

from typing import Any

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from jose import JWTError

from src.core.websocket import manager
from src.domains.identity.repository import IdentityRepository
from src.shared.auth import CurrentUser
from src.shared.auth.jwt import decode_access_token

from . import service
from .models import (
    MarkAllReadResponse,
    NotificationHistoryPage,
    NotificationHistoryStatus,
    NotificationInteractionCreate,
    NotificationInteractionResponse,
    NotificationItem,
    UnreadCountResponse,
)

router = APIRouter()


@router.get("", response_model=NotificationHistoryPage)
async def history(
    current_user: CurrentUser,
    cursor: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    status_filter: NotificationHistoryStatus = Query(default="all", alias="status"),
    category: str | None = None,
) -> NotificationHistoryPage:
    try:
        items, next_cursor, count = await service.list_history(
            user_id=current_user.id,
            limit=limit,
            cursor=cursor,
            status=status_filter,
            category=category,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return NotificationHistoryPage(
        items=[NotificationItem.model_validate(item) for item in items],
        next_cursor=next_cursor,
        unread_count=count,
    )


@router.get("/unread-count", response_model=UnreadCountResponse)
async def get_unread_count(current_user: CurrentUser) -> UnreadCountResponse:
    return UnreadCountResponse(unread_count=await service.unread_count(user_id=current_user.id))


@router.post("/read-all", response_model=MarkAllReadResponse)
async def read_all(current_user: CurrentUser) -> MarkAllReadResponse:
    updated, count = await service.mark_all_read(user_id=current_user.id)
    return MarkAllReadResponse(updated_count=updated, unread_count=count)


@router.post("/{notification_id}/read", status_code=204)
async def mark_read(notification_id: str, current_user: CurrentUser) -> Response:
    await service.mark_read(user_id=current_user.id, notification_id=notification_id)
    return Response(status_code=204)


@router.post("/{notification_id}/dismiss", status_code=204)
async def dismiss(notification_id: str, current_user: CurrentUser) -> Response:
    await service.dismiss(user_id=current_user.id, notification_id=notification_id)
    return Response(status_code=204)


@router.post(
    "/{notification_id}/interactions",
    response_model=NotificationInteractionResponse,
    status_code=201,
)
async def create_interaction(
    notification_id: str,
    body: NotificationInteractionCreate,
    current_user: CurrentUser,
) -> Any:
    try:
        row = await service.append_interaction(
            user_id=current_user.id, notification_id=notification_id, request=body
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    return row


async def _authenticated_user(token: str) -> Any:
    try:
        payload = decode_access_token(token)
    except JWTError:
        return None
    email = payload.get("sub")
    if payload.get("type") != "access" or not email:
        return None
    user = await IdentityRepository().find_by_email(str(email))
    return user if user is not None and user.is_active else None


@router.websocket("/ws")
async def notification_websocket(websocket: WebSocket, token: str = Query(...)) -> None:
    """Authenticated best-effort hints; clients reconcile through HTTP history/count."""

    user = await _authenticated_user(token)
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    connection_id = await manager.connect(websocket, user.id)
    try:
        while True:
            frame = await websocket.receive_json()
            if frame.get("type") == "ping":
                await manager.send_personal_message(connection_id, {"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(connection_id)
