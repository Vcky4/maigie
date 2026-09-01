"""Canonical notification HTTP and realtime API."""

import hashlib
from typing import Any

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from jose import JWTError

from src.core.websocket import manager
from src.domains.identity.repository import IdentityRepository
from src.shared.auth import CurrentUser, StaffUser
from src.shared.auth.jwt import decode_access_token
from src.shared.infrastructure.rate_limit import enforce_rate_limit

from . import service
from .models import (
    MarkAllReadResponse,
    MobilePushInstallationUpsert,
    NotificationHistoryPage,
    NotificationHistoryStatus,
    NotificationInteractionCreate,
    NotificationInteractionResponse,
    NotificationItem,
    NotificationSettingsResponse,
    NotificationSettingsUpdate,
    PushInstallationList,
    PushInstallationResponse,
    PushInstallationRevoke,
    UnreadCountResponse,
)

router = APIRouter()
push_installations_router = APIRouter()


@router.get("/operations/metrics", include_in_schema=False)
async def operational_metrics(_staff_user: StaffUser) -> dict[str, Any]:
    """Database-backed aggregate lifecycle metrics for authenticated staff."""

    return await service.lifecycle_metrics()


@push_installations_router.get("", response_model=PushInstallationList)
async def list_push_installations(current_user: CurrentUser) -> PushInstallationList:
    rows = await service.list_push_installations(user_id=current_user.id)
    return PushInstallationList(
        items=[PushInstallationResponse.model_validate(row) for row in rows]
    )


@push_installations_router.post(
    "/mobile", response_model=PushInstallationResponse, response_model_exclude_none=True
)
async def upsert_mobile_push_installation(
    body: MobilePushInstallationUpsert, current_user: CurrentUser
) -> PushInstallationResponse:
    row, revocation_secret = await service.upsert_mobile_push_installation(
        user_id=current_user.id, request=body
    )
    return PushInstallationResponse.model_validate(row).model_copy(
        update={"revocation_secret": revocation_secret}
    )


@push_installations_router.post("/revoke", status_code=204)
async def revoke_push_installation(body: PushInstallationRevoke, request: Request) -> Response:
    client_host = request.client.host if request.client is not None else "unknown"
    limiter_identity = hashlib.sha256(f"{client_host}:{body.installation_id}".encode()).hexdigest()
    await enforce_rate_limit(
        user_id=limiter_identity,
        endpoint="push_installation_revoke",
        max_requests=10,
        window_seconds=60,
    )
    await service.revoke_push_installation(
        installation_id=body.installation_id,
        revocation_secret=body.revocation_secret,
    )
    return Response(status_code=204)


@push_installations_router.delete("/{installation_id}", status_code=204)
async def disable_push_installation(installation_id: str, current_user: CurrentUser) -> Response:
    if not await service.disable_push_installation(
        user_id=current_user.id, installation_id=installation_id
    ):
        raise HTTPException(status_code=404, detail="Push installation not found")
    return Response(status_code=204)


@router.get("/settings", response_model=NotificationSettingsResponse)
async def get_notification_settings(current_user: CurrentUser) -> NotificationSettingsResponse:
    return await service.get_notification_settings(user_id=current_user.id)


@router.put("/settings", response_model=NotificationSettingsResponse)
async def put_notification_settings(
    body: NotificationSettingsUpdate, current_user: CurrentUser
) -> NotificationSettingsResponse:
    return await service.update_notification_settings(user_id=current_user.id, request=body)


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
