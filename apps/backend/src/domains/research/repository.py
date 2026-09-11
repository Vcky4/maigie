"""Reads and writes for educator survey responses.

Lookup on the public surface is **by token hash only** — the same rule as `LandingDraftRepository`, for
the same reason: an id appears in response bodies and logs, so if an id were enough to read a response
the token would be decoration.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Text, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.database import get_session_factory

from .db_models import EducatorSurveyContact, EducatorSurveyResponse

logger = logging.getLogger(__name__)


class EducatorSurveyRepository:
    """Persistence for the research domain."""

    async def _session(self) -> AsyncSession:
        return get_session_factory()()

    async def create(self, data: dict[str, Any]) -> EducatorSurveyResponse:
        async with await self._session() as session:
            row = EducatorSurveyResponse(**data)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def get_by_token_hash(self, token_hash: str) -> EducatorSurveyResponse | None:
        async with await self._session() as session:
            stmt = select(EducatorSurveyResponse).where(
                EducatorSurveyResponse.token_hash == token_hash
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    async def save_section(
        self, response_id: str, *, answers: dict[str, Any], last_section: int
    ) -> EducatorSurveyResponse | None:
        """Replace the answer map on a response that is still open.

        The `status = 'partial'` predicate is on the write, not only on the preceding token lookup:
        two browser tabs, or a resumed link opened after a submit, must not be able to append answers
        to a response that has already been submitted. The caller merges — the whole map is written at
        once so a section save is a single statement rather than a read-modify-write race.
        """
        async with await self._session() as session:
            result = await session.execute(
                update(EducatorSurveyResponse)
                .where(
                    EducatorSurveyResponse.id == response_id,
                    EducatorSurveyResponse.status == "partial",
                )
                .values(
                    answers=answers,
                    last_section=func.greatest(EducatorSurveyResponse.last_section, last_section),
                )
            )
            if not (result.rowcount or 0):
                await session.rollback()
                return None
            await session.commit()
            stmt = select(EducatorSurveyResponse).where(EducatorSurveyResponse.id == response_id)
            return (await session.execute(stmt)).scalar_one_or_none()

    async def mark_complete(self, response_id: str, *, answers: dict[str, Any]) -> bool:
        """Submit a response, once.

        Conditional on still being `partial`, so a double submit is the second caller losing a race
        rather than a second `submittedAt` overwriting the first.
        """
        async with await self._session() as session:
            result = await session.execute(
                update(EducatorSurveyResponse)
                .where(
                    EducatorSurveyResponse.id == response_id,
                    EducatorSurveyResponse.status == "partial",
                )
                .values(answers=answers, status="complete", submitted_at=datetime.now(UTC))
            )
            await session.commit()
            return bool(result.rowcount or 0)

    async def upsert_contact(self, response_id: str, detail: str | None) -> None:
        """Store, replace, or remove the follow-up contact detail.

        Separate call rather than a column on the response, so the only code path that writes an
        identity is one an auditor can find by name.
        """
        async with await self._session() as session:
            existing = (
                await session.execute(
                    select(EducatorSurveyContact).where(
                        EducatorSurveyContact.response_id == response_id
                    )
                )
            ).scalar_one_or_none()

            if detail is None or not detail.strip():
                if existing is not None:
                    await session.delete(existing)
                    await session.commit()
                return

            if existing is None:
                session.add(EducatorSurveyContact(response_id=response_id, detail=detail.strip()))
            else:
                existing.detail = detail.strip()
            await session.commit()

    async def get_contact(self, response_id: str) -> str | None:
        async with await self._session() as session:
            stmt = select(EducatorSurveyContact.detail).where(
                EducatorSurveyContact.response_id == response_id
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    # --- Admin ---

    async def list_for_admin(
        self,
        *,
        status: str | None = None,
        admin_status: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[EducatorSurveyResponse], list[str], int]:
        """A page of responses, the ids that have a contact detail, and the total.

        Contact presence is returned as a set of ids rather than the details themselves: the list view
        needs to show that a respondent is reachable, which is not the same as showing who they are.
        """
        async with await self._session() as session:
            filters = []
            if status:
                filters.append(EducatorSurveyResponse.status == status)
            if admin_status:
                filters.append(EducatorSurveyResponse.admin_status == admin_status)
            if search:
                # Free-text search across the answer map. Cast to text rather than reaching into
                # specific keys: the useful search is "who mentioned marking?", and which of the six
                # open questions they mentioned it in is not something a searcher knows in advance.
                needle = f"%{search.lower()}%"
                filters.append(
                    or_(
                        func.lower(func.cast(EducatorSurveyResponse.answers, Text)).like(needle),
                        EducatorSurveyResponse.id == search,
                    )
                )

            total = (
                await session.execute(
                    select(func.count()).select_from(EducatorSurveyResponse).where(*filters)
                )
            ).scalar_one()

            rows = list(
                (
                    await session.execute(
                        select(EducatorSurveyResponse)
                        .where(*filters)
                        .order_by(EducatorSurveyResponse.created_at.desc())
                        .limit(limit)
                        .offset(offset)
                    )
                )
                .scalars()
                .all()
            )

            with_contact: list[str] = []
            if rows:
                with_contact = list(
                    (
                        await session.execute(
                            select(EducatorSurveyContact.response_id).where(
                                EducatorSurveyContact.response_id.in_([r.id for r in rows])
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            return rows, with_contact, int(total)

    async def get_by_id(self, response_id: str) -> EducatorSurveyResponse | None:
        """Read by id, for the admin surface only.

        Named plainly because here it is legitimate: the caller is an authenticated admin, not a
        token holder. The public routes have no path to this method.
        """
        async with await self._session() as session:
            stmt = select(EducatorSurveyResponse).where(EducatorSurveyResponse.id == response_id)
            return (await session.execute(stmt)).scalar_one_or_none()

    async def set_admin_status(self, response_id: str, admin_status: str) -> bool:
        async with await self._session() as session:
            result = await session.execute(
                update(EducatorSurveyResponse)
                .where(EducatorSurveyResponse.id == response_id)
                .values(admin_status=admin_status)
            )
            await session.commit()
            return bool(result.rowcount or 0)


educator_survey_repo = EducatorSurveyRepository()
