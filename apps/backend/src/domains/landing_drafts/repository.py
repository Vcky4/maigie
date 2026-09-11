"""Landing drafts — persistence.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.database import get_session_factory

from .db_models import LandingDraft

logger = logging.getLogger(__name__)


class LandingDraftRepository:
    """Reads and writes `LandingDraft` rows.

    Lookup is **by token hash only**. There is deliberately no `get_by_id`: an id appears in a
    response body and in logs, and if it were sufficient to read a draft then the token would be
    decoration. Every public read therefore has to present the token, and the id is only ever used
    to confirm the caller is talking about the draft they think they are.
    """

    async def _session(self) -> AsyncSession:
        return get_session_factory()()

    async def create(self, data: dict[str, Any]) -> LandingDraft:
        async with await self._session() as session:
            draft = LandingDraft(**data)
            session.add(draft)
            await session.commit()
            await session.refresh(draft)
            return draft

    async def get_by_token_hash(self, token_hash: str) -> LandingDraft | None:
        async with await self._session() as session:
            stmt = select(LandingDraft).where(LandingDraft.token_hash == token_hash)
            return (await session.execute(stmt)).scalar_one_or_none()

    async def update_fields(self, draft_id: str, values: dict[str, Any]) -> LandingDraft | None:
        """Update a draft only while it is still open and unexpired.

        The condition belongs on the write, not only on the preceding token lookup: claim and PATCH
        can race, and a PATCH must never report success after claim has consumed the token.
        """
        now = datetime.now(UTC)
        async with await self._session() as session:
            if values:
                result = await session.execute(
                    update(LandingDraft)
                    .where(
                        LandingDraft.id == draft_id,
                        LandingDraft.status == "open",
                        LandingDraft.expires_at > now,
                    )
                    .values(**values)
                )
                if not (result.rowcount or 0):
                    await session.rollback()
                    return None
                await session.commit()
            stmt = select(LandingDraft).where(
                LandingDraft.id == draft_id,
                LandingDraft.status == "open",
                LandingDraft.expires_at > now,
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    async def get_by_id_internal(self, draft_id: str) -> LandingDraft | None:
        """Read by id, for internal callers that already proved token possession.

        Named awkwardly on purpose. It exists so `update_fields` can return the updated row, and the
        name is there to stop it becoming the lookup a future route reaches for — see the class
        docstring for why an id must not be enough to read a draft.
        """
        async with await self._session() as session:
            stmt = select(LandingDraft).where(LandingDraft.id == draft_id)
            return (await session.execute(stmt)).scalar_one_or_none()

    async def mark_claimed(self, draft_id: str, user_id: str) -> bool:
        """Claim a draft, once.

        **The single-use guarantee is this WHERE clause, not a preceding read.** A check-then-write
        would let two concurrent claims both see `open` and both proceed with profile application.
        Making the status transition itself the contended write means the database picks a single
        owning account; that owner may safely resume idempotent, generation-free setters after a
        transient failure, while every other account is refused.

        Returns True if this call is the one that claimed it.
        """
        now = datetime.now(UTC)
        async with await self._session() as session:
            result = await session.execute(
                update(LandingDraft)
                .where(
                    LandingDraft.id == draft_id,
                    LandingDraft.status == "open",
                    LandingDraft.expires_at > now,
                )
                .values(status="claimed", claimed_by=user_id, claimed_at=now)
            )
            await session.commit()
            return (result.rowcount or 0) > 0

    async def expire_due(self, *, limit: int = 500) -> int:
        """Mark expired drafts, for the sweep.

        Housekeeping only: reads already treat a past `expiresAt` as expired, so this changes what
        the table *says*, not what a visitor can do. It exists so the row's status is a fact rather
        than something every reader has to recompute, and so unclaimed-draft volume is countable.
        """
        now = datetime.now(UTC)
        async with await self._session() as session:
            due = (
                (
                    await session.execute(
                        select(LandingDraft.id)
                        .where(LandingDraft.status == "open", LandingDraft.expires_at <= now)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            if not due:
                return 0
            await session.execute(
                update(LandingDraft).where(LandingDraft.id.in_(list(due))).values(status="expired")
            )
            await session.commit()
            return len(due)

    async def redact_expired(self, *, now: datetime, limit: int = 500) -> int:
        """Clear the email address on drafts past their expiry. Returns rows changed.

        Separate from deletion, and earlier than it, because the two have different reasons. The
        email is the only personal data on the row, and the moment the draft stops being usable it
        stops having a purpose — so it goes at expiry, which makes the retention of the address
        exactly as long as the setup it was collected for.

        Runs on claimed rows too. A claimed draft keeps its row as the record that a conversion
        happened (see `db_models`), but the address is already on the account by then, so the copy
        here is duplicate personal data with no reader.
        """
        async with await self._session() as session:
            due = (
                (
                    await session.execute(
                        select(LandingDraft.id)
                        .where(
                            LandingDraft.expires_at <= now,
                            LandingDraft.email.is_not(None),
                        )
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            if not due:
                return 0
            await session.execute(
                update(LandingDraft).where(LandingDraft.id.in_(list(due))).values(email=None)
            )
            await session.commit()
            return len(due)

    async def delete_expired(self, *, cutoff: datetime, limit: int = 500) -> int:
        """Hard-delete unclaimed drafts whose expiry is older than `cutoff`. Returns rows removed.

        **Claimed drafts are never deleted here.** The row is the record that the marketing site
        converted someone, which is a fact about the site rather than about the account — the FK is
        `ON DELETE SET NULL` for the same reason. Their personal data is already gone via
        `redact_expired`, so what survives is a purpose, a timestamp, and a status.

        Deleting by a bounded set of ids rather than by predicate keeps each statement's lock
        footprint at `limit` rows, so a first sweep over a backlog cannot lock the range.
        """
        async with await self._session() as session:
            due = (
                (
                    await session.execute(
                        select(LandingDraft.id)
                        .where(
                            LandingDraft.status.in_(("open", "expired")),
                            LandingDraft.expires_at <= cutoff,
                        )
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            if not due:
                return 0
            result = await session.execute(
                delete(LandingDraft).where(LandingDraft.id.in_(list(due)))
            )
            await session.commit()
            return int(getattr(result, "rowcount", 0) or 0)


landing_draft_repo = LandingDraftRepository()
