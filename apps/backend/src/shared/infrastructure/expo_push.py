"""Expo Push Service adapter with normalized, token-safe outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from src.config import get_settings
from src.shared.infrastructure.http import create_http_client

MAX_EXPO_BATCH = 100
TRANSIENT_CODES = {"MessageRateExceeded", "ExpoServerError", "ProviderUnavailable"}
PERMANENT_DESTINATION_CODES = {"DeviceNotRegistered"}


@dataclass(frozen=True, slots=True)
class ExpoMessage:
    token: str
    title: str
    body: str
    data: dict[str, Any]
    channel_id: str
    badge: int


@dataclass(frozen=True, slots=True)
class ExpoTicketOutcome:
    ticket_id: str | None
    error_code: str | None = None
    error_detail: str | None = None
    retryable: bool = False
    disable_destination: bool = False


@dataclass(frozen=True, slots=True)
class ExpoReceiptOutcome:
    ticket_id: str
    delivered: bool
    pending: bool = False
    error_code: str | None = None
    error_detail: str | None = None
    retryable: bool = False
    disable_destination: bool = False


class ExpoPushAdapter:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        settings = get_settings()
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if settings.EXPO_ACCESS_TOKEN:
            headers["Authorization"] = f"Bearer {settings.EXPO_ACCESS_TOKEN}"
        self._owned_client = client is None
        self._client = client or create_http_client(
            base_url=settings.EXPO_PUSH_URL.rstrip("/") + "/",
            headers=headers,
        )

    async def __aenter__(self) -> ExpoPushAdapter:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def send(self, messages: list[ExpoMessage]) -> list[ExpoTicketOutcome]:
        outcomes: list[ExpoTicketOutcome] = []
        for start in range(0, len(messages), MAX_EXPO_BATCH):
            chunk = messages[start : start + MAX_EXPO_BATCH]
            payload = [
                {
                    "to": message.token,
                    "title": message.title,
                    "body": message.body,
                    "data": message.data,
                    "sound": "default",
                    "channelId": message.channel_id,
                    "badge": message.badge,
                }
                for message in chunk
            ]
            try:
                response = await self._client.post("send", json=payload)
                response.raise_for_status()
                body = response.json()
                tickets = body.get("data") if isinstance(body, dict) else None
                if not isinstance(tickets, list) or len(tickets) != len(chunk):
                    raise ValueError("Expo returned an invalid ticket count")
                outcomes.extend(self._normalize_ticket(ticket) for ticket in tickets)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                retryable = status == 429 or status >= 500
                outcomes.extend(
                    ExpoTicketOutcome(
                        ticket_id=None,
                        error_code=f"EXPO_HTTP_{status}",
                        error_detail=f"Expo HTTP {status}",
                        retryable=retryable,
                    )
                    for _message in chunk
                )
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                detail = self._safe_detail(exc)
                outcomes.extend(
                    ExpoTicketOutcome(
                        ticket_id=None,
                        error_code="EXPO_REQUEST_FAILED",
                        error_detail=detail,
                        retryable=True,
                    )
                    for _message in chunk
                )
        return outcomes

    async def receipts(self, ticket_ids: list[str]) -> dict[str, ExpoReceiptOutcome]:
        outcomes: dict[str, ExpoReceiptOutcome] = {}
        for start in range(0, len(ticket_ids), MAX_EXPO_BATCH):
            chunk = ticket_ids[start : start + MAX_EXPO_BATCH]
            try:
                response = await self._client.post("getReceipts", json={"ids": chunk})
                response.raise_for_status()
                body = response.json()
                receipts = body.get("data") if isinstance(body, dict) else None
                if not isinstance(receipts, dict):
                    raise ValueError("Expo returned an invalid receipt response")
                for ticket_id in chunk:
                    receipt = receipts.get(ticket_id)
                    outcomes[ticket_id] = (
                        self._normalize_receipt(ticket_id, receipt)
                        if isinstance(receipt, dict)
                        else ExpoReceiptOutcome(ticket_id=ticket_id, delivered=False, pending=True)
                    )
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                detail = self._safe_detail(exc)
                for ticket_id in chunk:
                    outcomes[ticket_id] = ExpoReceiptOutcome(
                        ticket_id=ticket_id,
                        delivered=False,
                        pending=True,
                        error_code="EXPO_RECEIPT_REQUEST_FAILED",
                        error_detail=detail,
                        retryable=True,
                    )
        return outcomes

    @staticmethod
    def _normalize_ticket(ticket: object) -> ExpoTicketOutcome:
        if not isinstance(ticket, dict):
            return ExpoTicketOutcome(None, "INVALID_TICKET", "Invalid Expo ticket", False)
        if ticket.get("status") == "ok" and isinstance(ticket.get("id"), str):
            return ExpoTicketOutcome(ticket_id=ticket["id"])
        details = ticket.get("details") if isinstance(ticket.get("details"), dict) else {}
        code = details.get("error") if isinstance(details.get("error"), str) else "EXPO_ERROR"
        message = ticket.get("message") if isinstance(ticket.get("message"), str) else None
        return ExpoTicketOutcome(
            ticket_id=None,
            error_code=code,
            error_detail=(message or "Expo rejected the message")[:500],
            retryable=code in TRANSIENT_CODES,
            disable_destination=code in PERMANENT_DESTINATION_CODES,
        )

    @staticmethod
    def _normalize_receipt(ticket_id: str, receipt: dict[str, Any]) -> ExpoReceiptOutcome:
        if receipt.get("status") == "ok":
            return ExpoReceiptOutcome(ticket_id=ticket_id, delivered=True)
        details = receipt.get("details") if isinstance(receipt.get("details"), dict) else {}
        code = details.get("error") if isinstance(details.get("error"), str) else "RECEIPT_ERROR"
        message = receipt.get("message") if isinstance(receipt.get("message"), str) else None
        return ExpoReceiptOutcome(
            ticket_id=ticket_id,
            delivered=False,
            error_code=code,
            error_detail=(message or "Expo reported a delivery error")[:500],
            retryable=code in TRANSIENT_CODES,
            disable_destination=code in PERMANENT_DESTINATION_CODES,
        )

    @staticmethod
    def _safe_detail(exc: Exception) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            return f"Expo HTTP {exc.response.status_code}"
        if isinstance(exc, httpx.TimeoutException):
            return "Expo request timed out"
        return exc.__class__.__name__
