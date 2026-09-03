"""Passes: inventory until activated, one at a time, ending on the clock or on the allowance.

MAIGIE_PLUS_COMMERCIAL_PLAN.md Decisions A, C, D and E. `entitlement_service` has known how to resolve
a pass since Phase 2 — `_compose`'s pass branch was written and tested against a shape with no table
behind it — so what is new here is the table, the service, and the two things that were seams:
`_active_pass` reading a real row, and `unitsUsed` making Decision E's allowance ending fire.

Most of the entitlement assertions are on `_active_pass`, which is pure. That is deliberate and is the
payoff of moving it off the old `_read_active_pass(user_id)` seam: Decision E's two endings are the
product decisions in this phase, and testing them against columns rather than a database is what makes
them cheap enough to test exhaustively.

Run with: SKIP_DB_FIXTURE=1 pytest tests/test_plus_passes.py -v
"""

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

from datetime import UTC, datetime, timedelta  # noqa: E402

import pytest  # noqa: E402

from src.domains.billing.services import entitlement_service as ent  # noqa: E402
from src.domains.billing.services import pass_service  # noqa: E402
from src.shared.exceptions import ConflictError, NotFoundError  # noqa: E402

LATER = datetime.now(UTC) + timedelta(hours=3)
EARLIER = datetime.now(UTC) - timedelta(minutes=1)


def a_pass_row(
    *,
    pass_id="pass_1",
    product_id="plus_pass_5h",
    expires_at=LATER,
    units_allowance=2_000,
    units_used=0,
):
    """The five columns `_active_pass` reads, as `resolve`'s joined query would hand them over."""
    return {
        "pass_id": pass_id,
        "product_id": product_id,
        "expires_at": expires_at,
        "units_allowance": units_allowance,
        "units_used": units_used,
    }


# ---------------------------------------------------------------------------
# The products
# ---------------------------------------------------------------------------


class TestTheProductTable:
    def test_the_five_hour_pass_is_two_thousand_units_not_three(self):
        """§8's `PlusPass` row still says `unitsAllowance` is "3 000 | 10 000". It is stale.

        Revision 10 moved the 5-hour pass 3 000 → 2 000 when the monthly went to $9.99, because at
        3 000 a $0.99 pass cost $0.00025/unit against the subscription's $0.00045 — the value product
        would have been the worst deal per unit, which is the inverted ladder that revision existed to
        fix. §6.3's table and `entitlement_service` both say 2 000.
        """
        assert pass_service.PASS_PRODUCTS["plus_pass_5h"].units_allowance == 2_000
        assert ent.WINDOW_ALLOWANCE_PASS_5H == 2_000

    def test_the_term_pass_total_follows_the_margin_table_not_the_window_table(self):
        """The two tables disagree and only one of them is survivable.

        §6.3 says the Term Pass gets "20 000/month", which over its four months is 80 000 units —
        $8.00 of COGS against $3.65 of net revenue, a loss on every sale. §6.8's margin table, which is
        where the NGN-only product actually lives, says 20 000 units at a 45% floor margin. Decision Q
        says a product's ceiling COGS is its allowance cap × $0.0001, so the two statements cannot both
        be true and the coherent one is the total.
        """
        term = pass_service.PASS_PRODUCTS["plus_pass_term"]
        assert term.units_allowance == 20_000
        # $0.0001 per unit: the ceiling has to leave a margin against $3.65 blended net.
        assert term.units_allowance * 0.0001 < 3.65

    def test_a_voice_pack_is_not_a_pass(self):
        """Decision R. A voice pack is a balance on `User`; a `PlusPass` row for one would grant an
        entitlement the pack did not sell. Phase 5's rail routes on this rather than on a string
        comparison it can get wrong.
        """
        assert pass_service.is_pass_product("plus_pass_5h") is True
        assert pass_service.is_pass_product("plus_voice_30") is False
        assert "plus_voice_30" not in pass_service.PASS_PRODUCTS

    def test_every_pass_product_has_a_window_allowance_and_voice_grant(self):
        """Three tables keyed by the same ids, and a product missing from one of them fails in a way
        nobody would look for: `_compose` falls back to the *smallest* grant, so the learner quietly
        gets a 5-hour pass's allowance on a four-month product.
        """
        for product_id in pass_service.PASS_PRODUCTS:
            assert product_id in ent.WINDOW_ALLOWANCE_BY_PASS_PRODUCT, product_id
            assert product_id in ent.VOICE_SECONDS_BY_PASS_PRODUCT, product_id


# ---------------------------------------------------------------------------
# Decision E: two endings
# ---------------------------------------------------------------------------


class TestAPassEndsTwoWays:
    def test_a_running_pass_resolves(self):
        assert ent._active_pass(**a_pass_row()) is not None

    def test_the_wall_clock_ends_it(self):
        assert ent._active_pass(**a_pass_row(expires_at=EARLIER)) is None

    def test_a_spent_allowance_ends_it(self):
        """Decision E's second ending, and the one that stops the pass being a product that loses money
        the more it is used: five hours of continuous live voice is about $6.00 of inference against
        $0.75 of net revenue. The promise is capabilities without limit, usage with a stated ceiling.
        """
        assert ent._active_pass(**a_pass_row(units_used=2_000)) is None

    def test_an_overshot_allowance_ends_it(self):
        """`record_units` charges after the fact, so a pass can be exceeded by one operation in flight.
        The comparison is `>=` rather than `==` for that reason.
        """
        assert ent._active_pass(**a_pass_row(units_used=2_400)) is None

    def test_one_unit_short_still_runs(self):
        assert ent._active_pass(**a_pass_row(units_used=1_999)) is not None

    def test_both_endings_resolve_before_any_sweep(self):
        """The sweep runs every five minutes and writes the status. It is **not** what makes a pass end.

        A learner must never be granted Plus by a pass that ended four minutes ago because a job has not
        caught up, so expiry is applied on read. What the sweep adds is the durable status and the
        notification — Decision E: "a learner whose pass ended must be told, and nothing tells them if
        nothing runs."
        """
        # Neither of these rows has been swept: status is irrelevant to `_active_pass`, which never
        # reads it. Both are already over.
        assert ent._active_pass(**a_pass_row(expires_at=EARLIER)) is None
        assert ent._active_pass(**a_pass_row(units_used=9_999)) is None

    def test_no_pass_is_no_pass(self):
        assert ent._active_pass(**a_pass_row(pass_id=None)) is None
        assert ent._active_pass(**a_pass_row(expires_at=None)) is None

    def test_a_naive_expiry_does_not_raise(self):
        """The column is `DateTime(timezone=True)`, but a naive value from a fixture would raise on
        comparison rather than answer the question — the same defence `_subscription_lapsed` carries.
        """
        naive = (datetime.now(UTC) + timedelta(hours=1)).replace(tzinfo=None)
        assert ent._active_pass(**a_pass_row(expires_at=naive)) is not None


class TestThePassReachesTheEntitlement:
    def test_a_pass_grants_plus_with_its_own_allowances(self):
        entitlement = ent._compose(
            subscription_tier="FREE",
            subscription_period_end=None,
            active_pass=ent._active_pass(**a_pass_row()),
            active_trial=None,
        )
        assert entitlement.tier == "plus"
        assert entitlement.source == "pass"
        assert entitlement.window_allowance == ent.WINDOW_ALLOWANCE_PASS_5H
        assert entitlement.voice_seconds_included == ent.VOICE_SECONDS_PASS_5H
        assert entitlement.pass_id == "pass_1"

    def test_an_ended_pass_grants_nothing(self):
        entitlement = ent._compose(
            subscription_tier="FREE",
            subscription_period_end=None,
            active_pass=ent._active_pass(**a_pass_row(units_used=2_000)),
            active_trial=None,
        )
        assert entitlement.tier == "free"
        assert entitlement.pass_id is None

    def test_a_subscription_still_outranks_a_pass(self):
        """Decision B's precedence, re-asserted now that a pass can actually exist. A subscriber's pass
        must not be silently burned by a request they were already entitled to make — which is also why
        Decision D refuses the activation rather than allowing it and letting it run down.
        """
        entitlement = ent._compose(
            subscription_tier="PREMIUM_MONTHLY",
            subscription_period_end=LATER,
            active_pass=ent._active_pass(**a_pass_row()),
            active_trial=None,
        )
        assert entitlement.source == "subscription"

    def test_the_pass_is_the_voice_grant_source(self):
        """Phase 3's lazy voice re-derivation keys on this. When the pass stops being the active
        entitlement the source id stops matching, and the next read discards the granted minutes — which
        is what replaced the sweep the plan originally wanted for voice.
        """
        entitlement = ent._compose(
            subscription_tier="FREE",
            subscription_period_end=None,
            active_pass=ent._active_pass(**a_pass_row()),
            active_trial=None,
        )
        assert entitlement.voice_allowance_source_id == "pass:pass_1"


# ---------------------------------------------------------------------------
# Decision D: activation refusals
# ---------------------------------------------------------------------------


class TestActivationIsRefusedWhenPlusIsAlreadyActive:
    """Decision D: refused, not queued and not stacked. The learner keeps the pass every time.

    Queuing would make "how long am I Plus for" unanswerable at a glance and turn expiry ordering into a
    support queue. Three sources can refuse, and they are three different situations for the learner —
    only one of which is their own doing — so the code carries three messages rather than one.
    """

    @staticmethod
    def _resolve_as(monkeypatch, entitlement):
        async def fake_resolve(_user_id):
            return entitlement

        monkeypatch.setattr(ent, "resolve", fake_resolve)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("source", "entitlement_kwargs"),
        [
            (
                "subscription",
                {"subscription_tier": "PREMIUM_MONTHLY", "subscription_period_end": LATER},
            ),
            ("trial", {"active_trial": ent.ActiveTrial(ends_at=LATER, days_remaining=2)}),
        ],
    )
    async def test_each_reason_refuses_with_pass_redundant(
        self, monkeypatch, source, entitlement_kwargs
    ):
        kwargs = {
            "subscription_tier": "FREE",
            "subscription_period_end": None,
            "active_pass": None,
            "active_trial": None,
        }
        kwargs.update(entitlement_kwargs)
        self._resolve_as(monkeypatch, ent._compose(**kwargs))

        with pytest.raises(ConflictError) as raised:
            await pass_service.activate(user_id="u1", pass_id="pass_1")
        assert raised.value.code == "PASS_REDUNDANT"
        assert f"active_source={source}" in raised.value.detail

    @pytest.mark.asyncio
    async def test_another_active_pass_refuses_too(self, monkeypatch):
        self._resolve_as(
            monkeypatch,
            ent._compose(
                subscription_tier="FREE",
                subscription_period_end=None,
                active_pass=ent._active_pass(**a_pass_row(pass_id="already_running")),
                active_trial=None,
            ),
        )
        with pytest.raises(ConflictError) as raised:
            await pass_service.activate(user_id="u1", pass_id="pass_2")
        assert raised.value.code == "PASS_REDUNDANT"
        assert "active_source=pass" in raised.value.detail

    @pytest.mark.asyncio
    async def test_the_refusal_reads_differently_per_reason(self, monkeypatch):
        """A learner who already subscribes and a learner mid-pass need different sentences. "You
        already have Plus" is unhelpful to the second, whose real question is what happens to the pass
        they just tried to start.
        """
        messages = set()
        for kwargs in (
            {"subscription_tier": "PREMIUM_MONTHLY", "subscription_period_end": LATER},
            {"active_trial": ent.ActiveTrial(ends_at=LATER, days_remaining=2)},
            {"active_pass": ent._active_pass(**a_pass_row())},
        ):
            base = {
                "subscription_tier": "FREE",
                "subscription_period_end": None,
                "active_pass": None,
                "active_trial": None,
            }
            base.update(kwargs)
            self._resolve_as(monkeypatch, ent._compose(**base))
            with pytest.raises(ConflictError) as raised:
                await pass_service.activate(user_id="u1", pass_id="pass_1")
            messages.add(raised.value.message)
        assert len(messages) == 3

    @pytest.mark.asyncio
    async def test_a_refused_activation_reads_no_row(self, monkeypatch):
        """The learner keeps the pass, and this is the structural version of that claim: the refusal
        happens before the pass is even loaded, so there is nothing to accidentally mutate.
        """
        self._resolve_as(
            monkeypatch,
            ent._compose(
                subscription_tier="PREMIUM_MONTHLY",
                subscription_period_end=LATER,
                active_pass=None,
                active_trial=None,
            ),
        )

        def forbidden():
            raise AssertionError("a refused activation must not open a session")

        monkeypatch.setattr(pass_service, "get_session_factory", forbidden)
        with pytest.raises(ConflictError):
            await pass_service.activate(user_id="u1", pass_id="pass_1")


# ---------------------------------------------------------------------------
# The invariant, and where it lives
# ---------------------------------------------------------------------------


class TestTheOneActiveInvariantBelongsToTheDatabase:
    def test_the_partial_unique_index_exists_on_the_model(self):
        """Decision A. Two concurrent activations must produce one winner and one `409`, which a
        pre-check cannot deliver — it loses the race it exists to prevent. The index is the invariant;
        the pre-check in `activate` only makes the common case explain itself.
        """
        from src.domains.billing.db_models import PlusPass

        index = next(
            arg
            for arg in PlusPass.__table_args__
            if getattr(arg, "name", None) == "PlusPass_oneActivePerUser_idx"
        )
        assert index.unique is True
        assert [column.name for column in index.columns] == ["userId"]
        # Partial: without the predicate this would forbid a learner ever holding two passes at all,
        # including two in inventory, which is the opposite of Decision A.
        assert "status = 'active'" in str(index.dialect_kwargs["postgresql_where"])

    def test_the_service_translates_the_race_rather_than_preventing_it(self):
        """`activate` catches `IntegrityError` and answers the same `409` as the sequential case. If it
        stopped doing that, the loser of a race would surface a 500 instead of a refusal.
        """
        import inspect

        source = inspect.getsource(pass_service.activate)
        assert "IntegrityError" in source
        assert "PASS_REDUNDANT" in source


class TestGrant:
    @pytest.mark.asyncio
    async def test_a_non_pass_product_is_refused_rather_than_granted(self):
        """Reaching here with `plus_voice_30` means a product was sold that cannot be granted as a pass,
        which is a defect in the rail rather than a bad request from a learner — hence a named conflict
        rather than a validation error.
        """
        with pytest.raises(ConflictError) as raised:
            await pass_service.grant(user_id="u1", product_id="plus_voice_30")
        assert raised.value.code == "NOT_A_PASS_PRODUCT"

    def test_grant_snapshots_rather_than_referencing(self):
        """`durationMinutes` and `unitsAllowance` are written onto the row. Re-pricing or re-timing a
        product must not change a pass already sold, and it is also what lets §6.8's NGN allowances be a
        property of the purchase rather than a currency branch in every reader.
        """
        import inspect

        source = inspect.getsource(pass_service.grant)
        assert "duration_minutes=duration_minutes or product.duration_minutes" in source
        assert "units_allowance=units_allowance or product.units_allowance" in source


class TestEnding:
    def test_expire_only_clears_the_cache_it_owns(self):
        """The denormalised columns have one writer, and a blind clear would wipe a *different* pass the
        learner activated in the meantime. That is the failure mode a cached column invites.
        """
        import inspect

        source = inspect.getsource(pass_service.expire)
        assert "user.active_plus_pass_id == row.id" in source

    def test_expire_is_idempotent(self):
        """The sweep runs every five minutes and lazy expiry has already made the pass free on read, so
        re-ending an ended pass has to be a no-op rather than an error.
        """
        import inspect

        source = inspect.getsource(pass_service.expire)
        assert "row.status != STATUS_ACTIVE" in source

    def test_the_two_reasons_stay_distinct(self):
        assert pass_service.REASON_EXPIRED != pass_service.REASON_EXHAUSTED

    def test_the_sweep_does_not_touch_the_voice_balance(self):
        """The plan asked for it to, and Phase 3 replaced that with lazy re-derivation. Anyone adding
        voice here would be fighting `voice_service.resolve` and would reintroduce exactly the interval
        the lazy design removes — one in which an ended pass's minutes are still spendable.
        """
        import inspect

        from src.workers import billing_tasks

        source = inspect.getsource(billing_tasks.sweep_expired_passes_task)
        for forbidden in ("voiceSecondsRemaining", "voice_service", "voiceAllowanceSourceId"):
            assert forbidden not in source.split('"""')[2], forbidden


class TestScope:
    def test_a_pass_carries_no_space_scope(self):
        """Decision F, enforced by not writing the code. A pass is personal-scope: a learner holding one
        gets Plus models in their own workspace and free-tier models inside a Space unless that Space
        assigned them a Plus seat. The paywall copy says "your personal workspace" for this reason.
        """
        import inspect

        source = inspect.getsource(pass_service)
        for forbidden in ("space_id", "seat_tier", "learning_spaces", "SpaceMember"):
            assert forbidden not in source, forbidden

    def test_resolve_still_takes_a_user_id_and_nothing_else(self):
        """The pass table existing is the moment someone might be tempted to add a scope argument."""
        import inspect

        params = inspect.signature(ent.resolve).parameters
        assert list(params) == ["user_id"]
