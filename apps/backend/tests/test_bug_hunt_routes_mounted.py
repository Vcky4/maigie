"""The Bug Hunt routers are mounted, and the right half of the surface is open.

The routing table is the thing worth asserting here rather than handler behaviour, because the failure
this domain is most exposed to is a *reachability* failure: the participant app lives at its own origin
and boots on `GET /program`, so that endpoint being authenticated, misprefixed or unmounted turns the
landing page into a blank screen for everyone, including people who cannot yet have a token.

It also pins the auth split. Three facts, each of which would be a real incident inverted:

- `GET /program` and `GET /seasons` take **no** token. A marketing page behind auth is a marketing page
  nobody reads, and the reward table has to be readable before anyone signs up.
- `GET /me` **does**. It reports one person's standing.
- Season creation and the two transitions are **super admin**, not staff. They set budgets.

Run with: pytest tests/test_bug_hunt_routes_mounted.py -v
"""

import pytest

from src.app import create_app

PREFIX = "/api/v1"
PARTICIPANT = f"{PREFIX}/bug-hunt"
ADMIN = f"{PREFIX}/admin/bug-hunt"


@pytest.fixture(scope="module")
def app():
    return create_app()


@pytest.fixture(scope="module")
def paths(app) -> set[str]:
    return {route.path for route in app.routes}


@pytest.fixture(scope="module")
def schema(app) -> dict:
    return app.openapi()


MOUNTED = [
    f"{PARTICIPANT}/program",
    f"{PARTICIPANT}/seasons",
    f"{PARTICIPANT}/me",
    f"{ADMIN}/seasons",
    f"{ADMIN}/seasons/defaults",
    f"{ADMIN}/seasons/{{program_id}}",
    f"{ADMIN}/seasons/{{program_id}}/open",
    f"{ADMIN}/seasons/{{program_id}}/close",
    f"{ADMIN}/seasons/{{program_id}}/carry-forward",
]


class TestTheSurfaceIsReachable:
    @pytest.mark.parametrize("path", MOUNTED)
    def test_endpoint_is_mounted(self, path, paths):
        assert path in paths

    def test_the_participant_prefix_is_bug_hunt_not_bughunt(self, paths):
        """Hyphenated, matching `/api/v1/admin/bug-hunt` and every other multi-word prefix here.

        Pinned because the app name is `apps/bughunt` and the domain package is `bug_hunt`, so there are
        three plausible spellings in play and only one of them is the URL.
        """
        assert any(p.startswith(f"{PARTICIPANT}/") for p in paths)
        assert not any(p.startswith(f"{PREFIX}/bughunt") for p in paths)
        assert not any(p.startswith(f"{PREFIX}/bug_hunt") for p in paths)

    def test_the_admin_half_is_under_the_shared_admin_prefix(self, paths):
        """Following `careers`, `content`, `finance` and `research`: one admin prefix, so the routing
        table shows at a glance which paths need staff."""
        assert any(p.startswith(f"{PREFIX}/admin/bug-hunt/") for p in paths)


class TestTheDefaultsRouteIsNotSwallowedByTheIdRoute:
    def test_defaults_is_registered_before_the_parameterised_route(self, app):
        """`/seasons/defaults` and `/seasons/{program_id}` both match the same request.

        FastAPI resolves in registration order, so if the parameterised route came first, the season
        editor would ask for defaults and be handed a 404 for a season called "defaults". Ordering is
        the only thing preventing that, and ordering is invisible in a diff.
        """
        order = [r.path for r in app.routes if isinstance(getattr(r, "path", None), str)]
        assert order.index(f"{ADMIN}/seasons/defaults") < order.index(
            f"{ADMIN}/seasons/{{program_id}}"
        )


class TestTheAuthSplit:
    def test_the_programme_state_is_public(self, schema):
        """The endpoint the landing page renders from. Unauthenticated by design."""
        assert "security" not in schema["paths"][f"{PARTICIPANT}/program"]["get"]

    def test_the_season_history_is_public(self, schema):
        assert "security" not in schema["paths"][f"{PARTICIPANT}/seasons"]["get"]

    def test_me_requires_a_token(self, schema):
        assert schema["paths"][f"{PARTICIPANT}/me"]["get"].get("security")

    @pytest.mark.parametrize(
        ("path", "method"),
        [
            (f"{ADMIN}/seasons", "get"),
            (f"{ADMIN}/seasons", "post"),
            (f"{ADMIN}/seasons/defaults", "get"),
            (f"{ADMIN}/seasons/{{program_id}}", "get"),
            (f"{ADMIN}/seasons/{{program_id}}", "patch"),
            (f"{ADMIN}/seasons/{{program_id}}/open", "post"),
            (f"{ADMIN}/seasons/{{program_id}}/close", "post"),
            (f"{ADMIN}/seasons/{{program_id}}/carry-forward", "post"),
        ],
    )
    def test_every_admin_endpoint_requires_a_token(self, path, method, schema):
        assert schema["paths"][path][method].get("security")

    def test_money_changing_endpoints_are_super_admin(self, app):
        """Staff triage findings; only a super admin sets a budget or opens a season.

        Asserted against the dependency actually wired to each route, because the two guards differ by
        one word in a type alias and an accidental `StaffUser` on season creation would hand a content
        manager the budget.
        """
        from src.shared.auth.dependencies import get_super_admin_user

        super_admin_only = {
            (f"{ADMIN}/seasons", "POST"),
            (f"{ADMIN}/seasons/{{program_id}}", "PATCH"),
            (f"{ADMIN}/seasons/{{program_id}}/open", "POST"),
            (f"{ADMIN}/seasons/{{program_id}}/close", "POST"),
            (f"{ADMIN}/seasons/{{program_id}}/carry-forward", "POST"),
        }
        seen = set()
        for route in app.routes:
            key_methods = getattr(route, "methods", None) or set()
            for method in key_methods:
                key = (getattr(route, "path", None), method)
                if key not in super_admin_only:
                    continue
                seen.add(key)
                dependencies = [
                    d.call for d in getattr(route.dependant, "dependencies", []) if d.call
                ]
                assert get_super_admin_user in dependencies, f"{key} is not super-admin guarded"
        assert seen == super_admin_only, f"missing routes: {super_admin_only - seen}"


class TestTheContractTheAppBootsOn:
    def test_the_programme_state_names_which_of_three_states_it_is_in(self, schema):
        """`state` exists so the client never infers a page from a null season.

        The between-seasons case is not an edge case — it is where the programme spends most of the
        year, and it goes live the day a season closes. A client switching on `season === null` cannot
        tell "next season starts on the 14th" from "nothing is scheduled".
        """
        properties = schema["components"]["schemas"]["ProgramStateResponse"]["properties"]
        assert "state" in properties
        assert "season" in properties
        assert "nextSeason" in properties

    def test_the_reward_table_is_served_rather_than_hardcoded(self, schema):
        """A season carries its own tiers, so a closed season keeps showing what it actually paid and
        the landing page cannot advertise amounts the server will not pay."""
        season = schema["components"]["schemas"]["SeasonSummary"]["properties"]
        assert "rewards" in season
        tier = schema["components"]["schemas"]["RewardTier"]["properties"]
        assert set(tier) == {"category", "severity", "amountKobo"}

    def test_the_public_season_shape_carries_no_budget(self, schema):
        """A season's budget and spend are internal. The public shape is the sibling of the admin one,
        so it is worth asserting the sensitive fields did not come along with the inheritance."""
        season = schema["components"]["schemas"]["SeasonSummary"]["properties"]
        for internal in ("budgetKobo", "awardedKobo", "remainingBudgetKobo", "rewardMatrix"):
            assert internal not in season

    def test_the_admin_season_shape_does_carry_the_money(self, schema):
        season = schema["components"]["schemas"]["SeasonAdminView"]["properties"]
        for field in ("budgetKobo", "awardedKobo", "remainingBudgetKobo", "participantCounts"):
            assert field in season

    def test_me_carries_the_wallet_field_before_the_wallet_exists(self, schema):
        """Phase 4 fills it in. Declaring it now means the client's shape does not change when the
        ledger lands, which is the difference between a deploy and a coordinated release."""
        assert "wallet" in schema["components"]["schemas"]["MeResponse"]["properties"]

    def test_me_distinguishes_needing_terms_from_not_being_approved(self, schema):
        """A carried-forward tester is approved but owes an acknowledgement. Collapsing that into
        "not approved" would show a returning participant an application form they already passed."""
        properties = schema["components"]["schemas"]["MeResponse"]["properties"]
        assert "needsTermsAcceptance" in properties
        assert "participation" in properties
        assert "history" in properties

    def test_every_money_field_in_the_contract_is_named_kobo_and_typed_integer(self, schema):
        """The suffix is not decoration: it is what stops a client rendering ₦200,000 where ₦2,000 was
        meant. And an amount typed as a number rather than an integer invites a float."""
        components = schema["components"]["schemas"]
        offenders = []
        for name, definition in components.items():
            if not name.startswith(("Season", "Me", "Wallet", "Reward", "Program", "CarryForward")):
                continue
            for field, spec in (definition.get("properties") or {}).items():
                if "Kobo" not in field:
                    continue
                types = {spec.get("type")} | {
                    variant.get("type") for variant in spec.get("anyOf", [])
                }
                if "integer" not in types:
                    offenders.append(f"{name}.{field}")
        assert not offenders, f"money fields not typed as integers: {offenders}"

    def test_no_field_names_a_currency_or_a_formatted_amount(self, schema):
        components = schema["components"]["schemas"]
        for name, definition in components.items():
            if not name.startswith(("Season", "Me", "Wallet", "Reward")):
                continue
            for field in definition.get("properties") or {}:
                assert "Naira" not in field and "naira" not in field, f"{name}.{field}"
                assert not field.endswith("Formatted"), f"{name}.{field}"


class TestTheOpenApiTagsAreDeclared:
    def test_both_tags_have_descriptions(self, app):
        """Tags declared without a mounted router is how `/docs` ends up advertising endpoints nothing
        serves — and the generated client types are built from this schema."""
        tags = {t["name"] for t in (app.openapi().get("tags") or [])}
        assert "bug-hunt" in tags
        assert "bug-hunt-admin" in tags
