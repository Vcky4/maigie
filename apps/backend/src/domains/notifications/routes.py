"""Canonical notification HTTP and realtime API."""

import hashlib
import json
from typing import Any

from fastapi import (
    APIRouter,
    Header,
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
from .email_webhooks import process_resend_event
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
    WebPushCapability,
    WebPushSubscriptionRevoke,
    WebPushSubscriptionUpsert,
)

router = APIRouter()
push_installations_router = APIRouter()
email_webhooks_router = APIRouter()


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


@push_installations_router.get("/web/capability", response_model=WebPushCapability)
async def web_push_capability(current_user: CurrentUser) -> WebPushCapability:
    """Whether to offer web push to this learner, and the key their browser must use.

    The client cannot work this out for itself: browser support is only half the question, and
    the other half — kill switch, VAPID configuration, rollout cohort — lives here.
    """

    return await service.get_web_push_capability(user_id=current_user.id)


@push_installations_router.post(
    "/web", response_model=PushInstallationResponse, response_model_exclude_none=True
)
async def upsert_web_push_subscription(
    body: WebPushSubscriptionUpsert, current_user: CurrentUser
) -> PushInstallationResponse:
    """Create or rotate this browser's subscription.

    Refused when web push is not available for this learner rather than stored for later. A
    stored subscription that nothing will ever send to would make the settings screen claim
    web push is on while it is not, and the capability endpoint exists so the client knows
    before asking.
    """

    if not service.web_push_available_for(current_user.id):
        raise HTTPException(status_code=403, detail="Web push is not available for this account")
    row, _revocation_secret = await service.upsert_web_push_subscription(
        user_id=current_user.id, request=body
    )
    # No revocation secret is returned: unlike a mobile logout, a browser can only unsubscribe
    # from a page that already holds a session, so there is nothing for the secret to solve.
    return PushInstallationResponse.model_validate(row)


@push_installations_router.post("/web/revoke", status_code=204)
async def revoke_web_push_subscription(
    body: WebPushSubscriptionRevoke, current_user: CurrentUser
) -> Response:
    """Disable this browser's subscription, named by endpoint.

    Answers 204 whether or not a row was active. The client calls this on logout and on
    permission withdrawal, and both are idempotent intentions rather than assertions that a
    subscription exists — a 404 here would only teach the client to ignore the response.
    """

    await service.revoke_web_push_subscription(
        user_id=current_user.id, endpoint=body.endpoint.strip()
    )
    return Response(status_code=204)


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


@router.post("/unsubscribe", status_code=200)
async def one_click_unsubscribe(request: Request, token: str = Query(...)) -> dict[str, str]:
    """RFC 8058 one-click unsubscribe. Unauthenticated by necessity.

    A mail provider POSTs here on the learner's behalf, from their infrastructure and without a
    session, so the signed token is the only proof of identity available. It must act
    immediately and without a confirmation step — a provider that gets a landing page instead
    of an action reports the sender as not honouring unsubscribes.

    Answers 200 even for a token that does not verify. The caller is a mail provider, not a
    person: telling it which tokens are real would turn this into an oracle, and a retry storm
    against a link a learner already used is worse than a quiet no-op.
    """

    client_host = request.client.host if request.client is not None else "unknown"
    await enforce_rate_limit(
        user_id=hashlib.sha256(client_host.encode()).hexdigest(),
        endpoint="notification_unsubscribe",
        max_requests=30,
        window_seconds=60,
    )
    await service.apply_unsubscribe(token=token)
    return {"status": "ok"}


@router.get("/unsubscribe", status_code=200)
async def unsubscribe_from_link(request: Request, token: str = Query(...)) -> dict[str, object]:
    """The same action for a person following the footer link.

    Kept separate from the POST because the two callers need different answers: a provider
    needs the act done and nothing else, while a person needs to know it worked and where to
    adjust it. The web app renders that page and calls nothing else.
    """

    client_host = request.client.host if request.client is not None else "unknown"
    await enforce_rate_limit(
        user_id=hashlib.sha256(client_host.encode()).hexdigest(),
        endpoint="notification_unsubscribe",
        max_requests=30,
        window_seconds=60,
    )
    applied = await service.apply_unsubscribe(token=token)
    return {"applied": applied, "settingsPath": "/settings?tab=notifications"}


@email_webhooks_router.post("/resend", status_code=200)
async def resend_webhook(
    request: Request,
    svix_id: str = Header(default="", alias="svix-id"),
    svix_timestamp: str = Header(default="", alias="svix-timestamp"),
    svix_signature: str = Header(default="", alias="svix-signature"),
) -> Response:
    """Receive Resend delivery, bounce, and complaint events.

    Malformed JSON and unverifiable signatures both answer 400 so the provider surfaces the
    problem in its own dashboard rather than retrying forever. Everything that verifies answers
    200, including an event type this system does not act on, because asking a provider to retry
    an event we deliberately ignore only wastes both sides' capacity.
    """

    body = await request.body()
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Malformed webhook body") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Malformed webhook body")

    result = await process_resend_event(
        body=body,
        svix_id=svix_id,
        svix_timestamp=svix_timestamp,
        svix_signature=svix_signature,
        payload=payload,
    )
    if not result.accepted:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return Response(status_code=200)


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
