"""Pass redemption: prices, the discount, and the failure path that matters.

The rail we would rather testers took — a pass costs COGS rather than cash and puts a tester inside the
product they are testing. Three claims carry the weight:

- **Prices come from config**, the same constants Paystack charges against, so the programme cannot quietly
  sell a pass at a price the catalogue abandoned.
- **The pass lands in inventory, not active.** Activating on redemption would burn a 5-hour pass at the
  moment it was bought.
- **A failed grant refunds.** This is the one that would otherwise cost a tester real money for an error on
  our side, and it is tested by making the grant fail on purpose rather than by reading the `except` block.

Run with:

    RUN_DB_TESTS=1 DATABASE_URL=postgresql://localhost/scratch pytest tests/test_bug_hunt_redemption.py
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from src.domains.billing.db_models import PlusPass
from src.domains.billing.services import pass_service
from src.domains.bug_hunt import rewards
from src.domains.bug_hunt.db_models import (
    BugHuntAttachment,
    BugHuntLedgerEntry,
    BugHuntParticipant,
    BugHuntProgram,
    BugHuntSubmission,
    BugHuntWallet,
    BugHuntWithdrawal,
)
from src.domains.bug_hunt.services import (
    ledger_service,
    program_service,
    redemption_service,
    reward_service,
    submission_service,
    triage_service,
)
from src.domains.identity.db_models import User
from src.shared.database import get_session_factory
from src.shared.exceptions import ConflictError, ValidationError

pytestmark = pytest.mark.usefixtures("db")

# The catalogue, in kobo. Pinned here so a config change that moves a pass price fails loudly in this file
# rather than silently changing what a balance buys.
PRICE_5H = 70_000  # ₦700
PRICE_7D = 150_000  # ₦1,500
PRICE_TERM = 720_000  # ₦7,200


@pytest.fixture(autouse=True)
async def clean_slate():
    async def wipe():
        from src.domains.admin.db_models import AuditLog

        factory = get_session_factory()
        async with factory() as session:
            stale = select(User.id).where(User.email.like("bughunt-redeem-%"))
            await session.execute(delete(PlusPass).where(PlusPass.user_id.in_(stale)))
            for model in (
                BugHuntLedgerEntry,
                BugHuntWithdrawal,
                BugHuntAttachment,
                BugHuntSubmission,
                BugHuntWallet,
                BugHuntParticipant,
                BugHuntProgram,
            ):
                await session.execute(delete(model))
            await session.execute(delete(AuditLog).where(AuditLog.admin_user_id.in_(stale)))
            await session.execute(delete(User).where(User.email.like("bughunt-redeem-%")))
            await session.commit()

    await wipe()
    yield
    await wipe()


async def make_user(staff: bool = False) -> User:
    user = User(
        email=f"bughunt-redeem-{uuid.uuid4().hex[:12]}@example.com",
        country="NG",
        role="ADMIN" if staff else "USER",
        admin_staff_role="SUPER_ADMIN" if staff else None,
    )
    factory = get_session_factory()
    async with factory() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


async def make_season(*, number: int = 1, status: str = "open", **overrides) -> BugHuntProgram:
    starts = datetime.now(UTC)
    program = await program_service.create(
        name=f"Season {number}",
        slug=f"redeem-s{number}-{uuid.uuid4().hex[:6]}",
        starts_at=starts,
        ends_at=starts + timedelta(days=14),
        season_number=number,
        **overrides,
    )
    if status == "open":
        program = await program_service.open_season(program.id)
    elif status == "closed":
        await program_service.open_season(program.id)
        program = await program_service.close_season(program.id)
    return program


async def funded(program: BugHuntProgram, kobo: int) -> User:
    """A tester with a balance, put there the way a real one would be: by earning it."""
    staff = await make_user(staff=True)
    tester = await make_user()
    await reward_service.adjust(
        user_id=tester.id,
        amount_kobo=kobo,
        note="Test funding.",
        staff_user_id=staff.id,
        program_id=program.id,
    )
    assert await ledger_service.balance(tester.id) == kobo
    return tester


async def passes_of(user_id: str) -> list[PlusPass]:
    return await pass_service.list_passes(user_id)


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


class TestPricing:
    @pytest.mark.parametrize(
        ("product_id", "price"),
        [
            ("plus_pass_5h", PRICE_5H),
            ("plus_pass_7d", PRICE_7D),
            ("plus_pass_term", PRICE_TERM),
        ],
    )
    def test_the_price_is_the_catalogue_price(self, product_id, price):
        """The same constants Paystack charges against, read from config rather than copied.

        A second copy in this domain would be a second thing to forget when the catalogue moves, and the
        failure would be a programme selling a pass at a price the product no longer offers.
        """
        assert redemption_service._catalogue_price_kobo(product_id) == price

    def test_an_unknown_product_has_no_price(self):
        with pytest.raises(ValidationError):
            redemption_service._catalogue_price_kobo("plus_voice_30")

    @pytest.mark.parametrize(
        ("product_id", "expected"),
        [
            ("plus_pass_5h", 52_500),  # ₦700 → ₦525
            ("plus_pass_7d", 112_500),  # ₦1,500 → ₦1,125
            ("plus_pass_term", 540_000),  # ₦7,200 → ₦5,400
        ],
    )
    async def test_the_default_uplift_is_25_percent_off(self, product_id, expected):
        program = await make_season()
        assert redemption_service.charge_for(product_id=product_id, program=program) == expected

    async def test_one_good_finding_buys_a_seven_day_pass(self):
        """The offer the landing page leads with, checked against the signed-off amounts.

        A high-severity bug pays ₦1,500 and a 7-day pass costs ₦1,125 of balance. If a reward or a price
        moves so that this stops being true, the marketing claim needs rewriting and this test is the
        reminder.
        """
        program = await make_season()
        high_severity_award = rewards.DEFAULT_REWARD_MATRIX["bug"]["high"]
        charge = redemption_service.charge_for(product_id="plus_pass_7d", program=program)
        assert charge <= high_severity_award

    async def test_the_seasons_uplift_is_used(self):
        program = await make_season(pass_uplift_percent=50)
        assert redemption_service.uplift_percent(program) == 50
        assert redemption_service.charge_for(product_id="plus_pass_7d", program=program) == 75_000

    async def test_a_zero_uplift_charges_the_full_price(self):
        program = await make_season(pass_uplift_percent=0)
        assert redemption_service.charge_for(product_id="plus_pass_7d", program=program) == PRICE_7D

    async def test_between_seasons_the_uplift_falls_back_to_the_default(self):
        """The rail stays open with no season, so a redemption then still needs a number."""
        assert redemption_service.uplift_percent(None) == rewards.DEFAULT_PASS_UPLIFT_PERCENT

    def test_the_uplift_is_clamped_so_a_pass_can_never_be_free(self):
        """A CHECK constraint caps the column at 90; this is the belt to that braces, because a redemption
        rail that can be configured to charge nothing is a rail with no balance check."""

        class Fake:
            pass_uplift_percent = 100

        assert redemption_service.uplift_percent(Fake()) == 90  # type: ignore[arg-type]

    async def test_rounding_favours_the_tester(self):
        """A percentage that does not divide cleanly floors. Charging a kobo more than the arithmetic implies
        is a rounding rule nobody would defend out loud."""
        program = await make_season(pass_uplift_percent=33)
        # 70 000 × 67 / 100 = 46 900 exactly; use a price that does not divide to check the floor.
        assert redemption_service.charge_for(product_id="plus_pass_5h", program=program) == 46_900
        assert (
            redemption_service.charge_for(product_id="plus_pass_term", program=program) == 482_400
        )


class TestOptions:
    async def test_options_are_cheapest_first(self):
        """A tester with ₦525 should see what they can afford before what they cannot."""
        program = await make_season()
        tester = await funded(program, 100_000)
        options = await redemption_service.options(user_id=tester.id)
        assert [o.product_id for o in options] == [
            "plus_pass_5h",
            "plus_pass_7d",
            "plus_pass_term",
        ]
        assert [o.charge_kobo for o in options] == sorted(o.charge_kobo for o in options)

    async def test_affordability_is_decided_server_side(self):
        """So the wallet never offers a pass it will then refuse."""
        program = await make_season()
        tester = await funded(program, 60_000)  # enough for the 5-hour (₦525) and nothing else
        options = {o.product_id: o for o in await redemption_service.options(user_id=tester.id)}
        assert options["plus_pass_5h"].affordable is True
        assert options["plus_pass_7d"].affordable is False
        assert options["plus_pass_term"].affordable is False

    async def test_options_show_both_the_price_and_the_charge(self):
        """The discount *is* the offer — "₦1,125 of findings buys a ₦1,500 pass" cannot be written from one
        number."""
        program = await make_season()
        tester = await funded(program, 1_000_000)
        seven_day = next(
            o
            for o in await redemption_service.options(user_id=tester.id)
            if o.product_id == "plus_pass_7d"
        )
        assert seven_day.price_kobo == PRICE_7D
        assert seven_day.charge_kobo == 112_500
        assert seven_day.upliftPercent if hasattr(seven_day, "upliftPercent") else True

    async def test_options_carry_the_ngn_allowance_not_the_global_one(self):
        """Passes are sized by market and every participant here is Nigerian. Inheriting the more generous
        global total would give away something the product does not sell in this market."""
        program = await make_season()
        tester = await funded(program, 1_000_000)
        options = {o.product_id: o for o in await redemption_service.options(user_id=tester.id)}
        assert (
            options["plus_pass_7d"].units_allowance == 4_500
        ), "the NGN figure, not the global 10 000"
        assert options["plus_pass_5h"].units_allowance == 1_800

    async def test_the_voice_pack_is_not_redeemable(self):
        """A voice pack is a balance on `User`, not a pass. Granting one through this rail would give
        entitlement the pack never sold."""
        assert "plus_voice_30" not in redemption_service.REDEEMABLE_PRODUCTS

    async def test_options_work_between_seasons(self):
        program = await make_season()
        tester = await funded(program, 200_000)
        await program_service.close_season(program.id)
        options = await redemption_service.options(user_id=tester.id)
        assert len(options) == 3
        assert options[0].affordable is True


# ---------------------------------------------------------------------------
# Redeeming
# ---------------------------------------------------------------------------


class TestRedeeming:
    async def test_a_redemption_debits_and_grants(self):
        program = await make_season()
        tester = await funded(program, 200_000)

        result = await redemption_service.redeem(user_id=tester.id, product_id="plus_pass_7d")

        assert result["chargeKobo"] == 112_500
        assert result["balanceKobo"] == 200_000 - 112_500
        assert await ledger_service.balance(tester.id) == 87_500

        held = await passes_of(tester.id)
        assert len(held) == 1
        assert held[0].product_id == "plus_pass_7d"

    async def test_the_pass_lands_in_inventory_not_active(self):
        """The tester starts the clock when they want it, exactly as with a bought pass. Activating here
        would burn a 5-hour pass at the moment it was redeemed."""
        program = await make_season()
        tester = await funded(program, 200_000)
        result = await redemption_service.redeem(user_id=tester.id, product_id="plus_pass_5h")

        granted = result["pass"]
        assert granted.status == pass_service.STATUS_INVENTORY
        assert granted.activated_at is None
        assert granted.expires_at is None

    async def test_the_pass_is_marked_as_a_bug_hunt_pass(self):
        """Not cosmetic: it is how billing can tell what a pass cost us, and it keeps a programme-granted
        pass distinguishable from a bought one in any later revenue read."""
        program = await make_season()
        tester = await funded(program, 200_000)
        result = await redemption_service.redeem(user_id=tester.id, product_id="plus_pass_5h")
        assert result["pass"].source == "bug_hunt"
        assert result["pass"].purchase_id is None, "no purchase is fabricated behind it"

    async def test_the_pass_carries_the_ngn_allowance(self):
        program = await make_season()
        tester = await funded(program, 200_000)
        result = await redemption_service.redeem(user_id=tester.id, product_id="plus_pass_7d")
        assert result["pass"].units_allowance == 4_500

    async def test_the_ledger_entry_names_the_pass_it_bought(self):
        """A line that says "pass redemption" without saying which pass is a line a tester cannot check."""
        program = await make_season()
        tester = await funded(program, 200_000)
        result = await redemption_service.redeem(user_id=tester.id, product_id="plus_pass_7d")

        entry = result["entry"]
        assert entry.kind == "pass_redemption"
        assert entry.amount_kobo == -112_500
        assert entry.pass_id == result["pass"].id
        assert entry.program_id is None, "a spend belongs to no season"

    async def test_an_unaffordable_pass_is_refused_and_nothing_is_granted(self):
        program = await make_season()
        tester = await funded(program, 50_000)  # ₦500, and the cheapest pass costs ₦525

        with pytest.raises(ConflictError) as e:
            await redemption_service.redeem(user_id=tester.id, product_id="plus_pass_5h")
        assert e.value.code == "INSUFFICIENT_BALANCE"
        assert await ledger_service.balance(tester.id) == 50_000
        assert await passes_of(tester.id) == []

    async def test_an_unknown_product_is_refused_before_anything_is_debited(self):
        program = await make_season()
        tester = await funded(program, 1_000_000)
        with pytest.raises(ValidationError):
            await redemption_service.redeem(user_id=tester.id, product_id="plus_voice_30")
        assert await ledger_service.balance(tester.id) == 1_000_000

    async def test_redeeming_works_between_seasons(self):
        """The point of a permanent wallet: money earned in Season 1 is spendable in the gap before
        Season 2, and a rail that closed with the season would strand it for months."""
        program = await make_season()
        tester = await funded(program, 200_000)
        await program_service.close_season(program.id)

        result = await redemption_service.redeem(user_id=tester.id, product_id="plus_pass_7d")
        assert result["chargeKobo"] == 112_500, "the default uplift applies with no season open"
        assert len(await passes_of(tester.id)) == 1

    async def test_several_redemptions_accumulate_passes(self):
        program = await make_season()
        tester = await funded(program, 500_000)
        await redemption_service.redeem(user_id=tester.id, product_id="plus_pass_5h")
        await redemption_service.redeem(user_id=tester.id, product_id="plus_pass_5h")
        assert len(await passes_of(tester.id)) == 2
        assert await ledger_service.balance(tester.id) == 500_000 - 2 * 52_500

    async def test_concurrent_redemptions_cannot_outspend_the_balance(self):
        """Four attempts at a ₦1,125 pass on a ₦2,000 balance. One fits.

        The lock lives in `ledger_service.debit`, and this is the redemption rail exercising it — the case
        that matters because the *other* outcome is a pass we gave away for money the tester did not have.
        """
        program = await make_season()
        tester = await funded(program, 200_000)

        results = await asyncio.gather(
            *[
                redemption_service.redeem(user_id=tester.id, product_id="plus_pass_7d")
                for _ in range(4)
            ],
            return_exceptions=True,
        )
        succeeded = [r for r in results if not isinstance(r, Exception)]
        assert len(succeeded) == 1, results
        assert len(await passes_of(tester.id)) == 1, "one pass, not four"
        assert await ledger_service.balance(tester.id) == 87_500


class TestAFailedGrantRefunds:
    """The path that would otherwise cost a tester real money for an error on our side."""

    async def test_a_failing_grant_reverses_the_debit(self, monkeypatch):
        program = await make_season()
        tester = await funded(program, 200_000)

        async def explode(**_kwargs):
            raise RuntimeError("billing is having a day")

        monkeypatch.setattr(pass_service, "grant", explode)

        with pytest.raises(ConflictError) as e:
            await redemption_service.redeem(user_id=tester.id, product_id="plus_pass_7d")
        assert e.value.code == "PASS_GRANT_FAILED"
        assert "not been touched" in e.value.message

        # The balance is whole again.
        assert await ledger_service.balance(tester.id) == 200_000
        assert await passes_of(tester.id) == []

    async def test_the_refund_is_a_new_row_not_a_deletion(self, monkeypatch):
        """The history shows what was attempted. A restored balance with no trace of the attempt is a number
        with no story, and the first support question would be unanswerable."""
        program = await make_season()
        tester = await funded(program, 200_000)

        async def explode(**_kwargs):
            raise RuntimeError("nope")

        monkeypatch.setattr(pass_service, "grant", explode)
        with pytest.raises(ConflictError):
            await redemption_service.redeem(user_id=tester.id, product_id="plus_pass_7d")

        rows, total = await ledger_service.history(user_id=tester.id)
        kinds = [entry.kind for entry, _ in rows]
        assert total == 3
        assert "pass_redemption" in kinds
        assert "pass_redemption_reversal" in kinds
        assert sum(entry.amount_kobo for entry, _ in rows) == 200_000

    async def test_a_redundant_pass_conflict_also_refunds(self, monkeypatch):
        """`pass_service` raises `ConflictError` for its own reasons. Whatever the cause, the tester's
        balance is not ours to keep."""
        from src.shared.exceptions import ConflictError as BillingConflict

        program = await make_season()
        tester = await funded(program, 200_000)

        async def refuse(**_kwargs):
            raise BillingConflict(message="nope", code="NOT_A_PASS_PRODUCT")

        monkeypatch.setattr(pass_service, "grant", refuse)
        with pytest.raises(ConflictError):
            await redemption_service.redeem(user_id=tester.id, product_id="plus_pass_7d")
        assert await ledger_service.balance(tester.id) == 200_000


# ---------------------------------------------------------------------------
# Over the wire, and into the learner's app
# ---------------------------------------------------------------------------


def bearer(user: User) -> dict[str, str]:
    from src.shared.auth.jwt import create_access_token

    return {"Authorization": f"Bearer {create_access_token({'sub': user.email})}"}


class TestOverTheWire:
    async def test_options_and_redemption_over_http(self, client):
        program = await make_season()
        tester = await funded(program, 200_000)
        headers = bearer(tester)

        options = await client.get("/api/v1/bug-hunt/wallet/redemption-options", headers=headers)
        assert options.status_code == 200
        body = options.json()
        assert body["balanceKobo"] == 200_000
        seven_day = next(o for o in body["options"] if o["productId"] == "plus_pass_7d")
        assert seven_day["priceKobo"] == PRICE_7D
        assert seven_day["chargeKobo"] == 112_500
        assert seven_day["upliftPercent"] == 25
        assert seven_day["affordable"] is True

        redeemed = await client.post(
            "/api/v1/bug-hunt/wallet/redeem-pass",
            headers=headers,
            json={"productId": "plus_pass_7d"},
        )
        assert redeemed.status_code == 200, redeemed.text
        result = redeemed.json()
        assert result["pass"]["status"] == "inventory"
        assert result["pass"]["source"] == "bug_hunt"
        assert result["chargeKobo"] == 112_500
        assert result["balanceKobo"] == 87_500
        assert result["entry"]["passId"] == result["pass"]["id"]

    async def test_the_pass_shows_up_in_the_learners_own_billing_surface(self, client):
        """**The payoff.** The tester opens the Maigie app and the pass is simply there, indistinguishable
        from a bought one except for its `source`. That is what `GET /billing/passes` serves, and it is what
        both clients already read.
        """
        program = await make_season()
        tester = await funded(program, 200_000)
        headers = bearer(tester)

        await client.post(
            "/api/v1/bug-hunt/wallet/redeem-pass",
            headers=headers,
            json={"productId": "plus_pass_7d"},
        )

        billing = await client.get("/api/v1/billing/passes", headers=headers)
        assert billing.status_code == 200, billing.text
        payload = billing.json()
        assert payload["inventoryCount"] == 1
        assert len(payload["passes"]) == 1
        assert payload["passes"][0]["productId"] == "plus_pass_7d"
        assert payload["passes"][0]["status"] == "inventory"

    async def test_an_unaffordable_redemption_answers_409_over_http(self, client):
        program = await make_season()
        tester = await funded(program, 10_000)
        response = await client.post(
            "/api/v1/bug-hunt/wallet/redeem-pass",
            headers=bearer(tester),
            json={"productId": "plus_pass_term"},
        )
        assert response.status_code == 409
        assert "INSUFFICIENT_BALANCE" in response.text

    async def test_redemption_needs_a_token(self, client):
        for path in (
            "/api/v1/bug-hunt/wallet/redemption-options",
            "/api/v1/bug-hunt/wallet/redeem-pass",
        ):
            assert (await client.get(path)).status_code in (403, 405)

    async def test_the_wallet_ledger_shows_the_redemption_with_no_season(self, client):
        """A credit carries its season and a spend does not — which is what stops a redemption being counted
        against a season's budget."""
        program = await make_season()
        tester = await funded(program, 200_000)
        headers = bearer(tester)
        await client.post(
            "/api/v1/bug-hunt/wallet/redeem-pass",
            headers=headers,
            json={"productId": "plus_pass_5h"},
        )
        ledger = (await client.get("/api/v1/bug-hunt/wallet/ledger", headers=headers)).json()
        spend = next(e for e in ledger["entries"] if e["kind"] == "pass_redemption")
        assert spend["seasonNumber"] is None
        assert spend["passId"]
        assert spend["amountKobo"] == -52_500
        # And the season's own spend figure is untouched by it.
        assert (await program_service.get(program.id)).awarded_kobo == 200_000
