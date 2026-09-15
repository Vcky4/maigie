"""A referral, walked end to end: signup on a code through to a pass in hand (§6.9, Decision O).

**Why this file exists separately from `test_points.py`.** That file's qualification tests set
`User.referred_by_code` by hand and start from there, so they proved the arithmetic while the step that
was actually broken sat upstream of every one of them. `SignupRequest.referral_code` was parsed and
then dropped by the route, `services.signup` had no parameter to receive it, and the
`BillingEvents.REFERRAL_LINKED` that `link_referral` emitted had no listener. Every unit was correct and
the chain was severed in three places, which is exactly the failure a suite of unit tests cannot see.

Measured in production before the fix: 60 recorded referral signups, and zero `PointsLedgerEntry` rows
of any kind.

So these tests start where a learner starts — `services.signup(referral_code=...)` — and refuse to
reach past a link in the chain. The one that matters most is
`test_the_whole_walk_from_signup_to_a_pass`: it never touches `referred_by_code` itself, so it fails if
any single step regresses.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.domains.billing.services import points_service

# ===========================================================================
# The wiring itself — no database, runs everywhere
# ===========================================================================


class TestTheEventHasSomewhereToLand:
    """`emit` returns silently when nothing is listening, so an unheard event looks identical to a
    handled one. This is the assertion that would have failed for the eight months the referral
    handler did not exist."""

    def test_referral_linked_has_a_registered_handler(self):
        from src.shared.events import BillingEvents
        from src.shared.events.bus import _handlers
        from src.shared.events.registry import register_handlers

        register_handlers()
        assert _handlers.get(BillingEvents.REFERRAL_LINKED), (
            "Nothing listens to billing.referral_linked. Signups on a code will be recorded nowhere "
            "and no referral can ever qualify."
        )

    def test_signup_accepts_a_referral_code(self):
        """The route parsed `referralCode` into `SignupRequest` and then called a `signup()` that had
        no parameter for it, so the field was accepted by the API and discarded. A signature check is
        a blunt test, but it is the one that catches the argument being dropped again."""
        import inspect

        from src.domains.identity import services

        assert "referral_code" in inspect.signature(services.signup).parameters

    def test_the_route_forwards_the_code_it_parsed(self):
        import inspect

        from src.domains.identity import routes

        source = inspect.getsource(routes.signup)
        assert (
            "referral_code=data.referral_code" in source
        ), "The signup route parses referralCode but does not pass it on."

    def test_native_callbacks_accept_and_forward_referral_code(self):
        import inspect

        from src.domains.identity import models, oauth_routes

        assert "referral_code" in models.NativeGoogleCallbackRequest.model_fields
        assert "referral_code" in models.NativeAppleCallbackRequest.model_fields

        google_src = inspect.getsource(oauth_routes.google_native_callback)
        assert "referral_code=data.referral_code" in google_src

        apple_src = inspect.getsource(oauth_routes.apple_native_callback)
        assert "referral_code=data.referral_code" in apple_src

    def test_oauth_registration_forwards_referral_code(self):
        import inspect

        from src.domains.identity import services

        source = inspect.getsource(services.get_or_create_oauth_user)
        assert "BillingEvents.REFERRAL_LINKED" in source
        assert "info.referral_code" in source


# ===========================================================================
# The walk — database-backed, opt-in via RUN_DB_TESTS
# ===========================================================================


@pytest.fixture
async def world(db, monkeypatch):
    """Real signups against a scratch database, with the outbound verification email stubbed.

    Requesting `db` is the suite's opt-in: `conftest.db_lifecycle` connects the engine and skips the
    test when `RUN_DB_TESTS` is unset.
    """
    from sqlalchemy import delete, select

    from src.domains.billing.db_models import ReferralReward, UsageEvent
    from src.domains.identity import emails as identity_emails
    from src.domains.identity import services as identity_services
    from src.domains.identity.db_models import User
    from src.shared.database.session import get_session_factory
    from src.shared.events.registry import register_handlers

    # Handlers are registered by `lifespan` in the web app and at import in the Celery app; a test
    # process is neither, and a referral that only works when some earlier test happened to import the
    # module is the bug this file exists for.
    register_handlers()

    async def _no_email(*args, **kwargs):
        return None

    monkeypatch.setattr(identity_emails, "send_verification_email", _no_email)

    factory = get_session_factory()
    tag = uuid.uuid4().hex[:8]
    created_emails: list[str] = []

    async def sign_up(*, referral_code: str | None = None) -> str:
        """Register through the real service and return the new user's id."""
        email = f"ref_{tag}_{uuid.uuid4().hex[:8]}@example.com"
        created_emails.append(email)
        user = await identity_services.signup(
            email=email,
            password="a-long-enough-password",
            name="Referral Test",
            referral_code=referral_code,
        )
        return user.id

    async def make_referrer() -> tuple[str, str]:
        """A learner with a minted referral code, which is how a real referrer gets one."""
        from src.domains.billing.services.referral_rewards_service import (
            get_or_create_referral_code,
        )

        user_id = await sign_up()
        return user_id, await get_or_create_referral_code(user_id)

    async def signup_rows(referred_user_id: str) -> int:
        """How many `signup` ReferralReward rows exist for this learner. Counted rather than fetched
        because the duplicate case is what matters."""
        async with factory() as session:
            rows = (
                await session.execute(
                    select(ReferralReward.id).where(
                        ReferralReward.referred_user_id == referred_user_id,
                        ReferralReward.reward_type == "signup",
                    )
                )
            ).all()
        return len(rows)

    async def referred_by_code(user_id: str) -> str | None:
        async with factory() as session:
            return (
                await session.execute(select(User.referred_by_code).where(User.id == user_id))
            ).scalar_one_or_none()

    async def study_for_days(user_id: str, days: int) -> None:
        """`days` distinct UTC days of billable operations, which is what qualification counts."""
        base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        async with factory() as session:
            for i in range(days):
                session.add(
                    UsageEvent(
                        user_id=user_id,
                        operation="chat_message",
                        units=100,
                        created_at=base + timedelta(days=i),
                    )
                )
            await session.commit()

    async def points_balance(user_id: str) -> int:
        return (await points_service.balance(user_id)).balance

    yield type(
        "World",
        (),
        {
            "factory": factory,
            "sign_up": staticmethod(sign_up),
            "make_referrer": staticmethod(make_referrer),
            "signup_rows": staticmethod(signup_rows),
            "referred_by_code": staticmethod(referred_by_code),
            "study_for_days": staticmethod(study_for_days),
            "points_balance": staticmethod(points_balance),
        },
    )

    async with factory() as session:
        if created_emails:
            ids = (
                (await session.execute(select(User.id).where(User.email.in_(created_emails))))
                .scalars()
                .all()
            )
            if ids:
                # UsageEvent has no FK to User, so it goes by id; the rest cascades with the users.
                await session.execute(delete(UsageEvent).where(UsageEvent.user_id.in_(ids)))
                await session.execute(delete(User).where(User.id.in_(ids)))
            await session.commit()


class TestSignupRecordsTheRelationship:
    @pytest.mark.asyncio
    async def test_a_signup_on_a_code_writes_the_row_qualification_reads(self, world):
        referrer_id, code = await world.make_referrer()

        referred_id = await world.sign_up(referral_code=code)

        assert await world.signup_rows(referred_id) == 1
        assert await world.referred_by_code(referred_id) == code

    @pytest.mark.asyncio
    async def test_signup_grants_nothing(self, world):
        """The 100 points wait on seven distinct days. Granting at signup is the farm-able version the
        gate exists to prevent, so an empty balance here is the guard, not an omission."""
        referrer_id, code = await world.make_referrer()

        await world.sign_up(referral_code=code)

        assert await world.points_balance(referrer_id) == 0

    @pytest.mark.asyncio
    async def test_a_lowercase_code_from_a_url_still_counts(self, world):
        """Codes are minted uppercase and arrive lowercased from share links and address bars. An
        exact match would lose a real referral with no error anywhere, which is the least visible way
        for this to fail."""
        referrer_id, code = await world.make_referrer()

        referred_id = await world.sign_up(referral_code=code.lower())

        assert await world.signup_rows(referred_id) == 1
        assert await world.referred_by_code(referred_id) == code

    @pytest.mark.asyncio
    async def test_a_code_pasted_with_whitespace_still_counts(self, world):
        referrer_id, code = await world.make_referrer()

        referred_id = await world.sign_up(referral_code=f"  {code} ")

        assert await world.signup_rows(referred_id) == 1

    @pytest.mark.asyncio
    async def test_an_unknown_code_does_not_fail_the_signup(self, world):
        """A mistyped code costs the referrer nothing and must not cost the learner an account."""
        referred_id = await world.sign_up(referral_code="ZZZZ9999")

        assert referred_id  # the account exists
        assert await world.signup_rows(referred_id) == 0
        assert await world.referred_by_code(referred_id) is None

    @pytest.mark.asyncio
    async def test_no_code_records_nothing(self, world):
        referred_id = await world.sign_up()

        assert await world.signup_rows(referred_id) == 0

    @pytest.mark.asyncio
    async def test_the_relationship_is_recorded_once_if_the_event_repeats(self, world):
        """A retried emit, or a learner who signs up on a code and then posts it to `link_referral`
        too, must not produce two rows: qualification walks this table and a duplicate would be a
        second chance to earn."""
        from src.shared.events import BillingEvents, emit

        referrer_id, code = await world.make_referrer()
        referred_id = await world.sign_up(referral_code=code)

        await emit(BillingEvents.REFERRAL_LINKED, {"user_id": referred_id, "referral_code": code})

        assert await world.signup_rows(referred_id) == 1

    @pytest.mark.asyncio
    async def test_a_self_referral_is_refused(self, world):
        """Only reachable through `link_referral` — at signup the account does not exist yet to own a
        code — but the handler is the shared path, so the refusal is asserted on it."""
        from src.shared.events import BillingEvents, emit

        user_id, code = await world.make_referrer()

        await emit(BillingEvents.REFERRAL_LINKED, {"user_id": user_id, "referral_code": code})

        assert await world.signup_rows(user_id) == 0
        assert await world.points_balance(user_id) == 0


class TestTheWholeWalk:
    @pytest.mark.asyncio
    async def test_the_whole_walk_from_signup_to_a_pass(self, world):
        """Signup on a code, seven days of study, 100 points, one 5h pass, nothing left over.

        Deliberately reaches past no step: the referral relationship is established only by
        `signup(referral_code=...)`, so a regression anywhere in the chain fails here rather than
        showing up as production data that never earns.
        """
        referrer_id, code = await world.make_referrer()
        referred_id = await world.sign_up(referral_code=code)

        # Six days is not yet enough, and the near-miss is worth asserting inside the walk: it shows
        # the seven-day gate is doing the work rather than the grant being unconditional.
        await world.study_for_days(referred_id, 6)
        assert await points_service.qualify_referral(referred_id) is None
        assert await world.points_balance(referrer_id) == 0

        await world.study_for_days(referred_id, 7)
        entry = await points_service.qualify_referral(referred_id)

        assert entry is not None
        assert await world.points_balance(referrer_id) == 100

        new_pass = await points_service.redeem(user_id=referrer_id, product_id="plus_pass_5h")

        assert new_pass.source == "points"
        assert new_pass.purchase_id is None  # points never touch the revenue ledger
        assert await world.points_balance(referrer_id) == 0

    @pytest.mark.asyncio
    async def test_the_nightly_job_finds_a_signup_it_did_not_write(self, world):
        """The beat task walks `ReferralReward` rows of type `signup` — the rows this fix started
        writing. Driving the task's own query proves signup produces something it can find, which is
        the join that was missing: 60 rows existed in production and the job had nothing to do with
        them because none of them came with usage.
        """
        from sqlalchemy import select

        from src.domains.billing.db_models import ReferralReward

        referrer_id, code = await world.make_referrer()
        referred_id = await world.sign_up(referral_code=code)
        await world.study_for_days(referred_id, 7)

        async with world.factory() as session:
            pending = (
                (
                    await session.execute(
                        select(ReferralReward.referred_user_id).where(
                            ReferralReward.reward_type == "signup"
                        )
                    )
                )
                .scalars()
                .all()
            )

        assert referred_id in pending

        for candidate in pending:
            if candidate == referred_id:
                assert await points_service.qualify_referral(candidate) is not None

        assert await world.points_balance(referrer_id) == 100
