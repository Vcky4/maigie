"""Grounded resource recommendation: searching, link checking, dedupe and persistence.

`recommend_resources` used to ask Gemini for URLs with no search tool attached and return the
reply. Two defects, and they compounded: it never looked anything up, so a share of the URLs
were invented and dead; and nothing was stored, so `Resource.isRecommended`,
`recommendationScore`, `recommendationSource` and `recommendationReason` — four columns that
exist for exactly this — had never been written by anything, one of them not even mapped by
`create_resource`.

The rewrite grounds the request in web search, resolves and checks every proposed URL, and
persists the survivors as real rows.

What is mocked and why: `generate_grounded_content` and `check_urls` both stand in for network
calls, so they are substituted. Everything below them is real — real repository, real SQLite,
real rows read back out — because the claim under test is "a recommendation becomes a correct
row", and asserting on a mocked repository would have passed against the old version too. The
old version composed a perfectly well-formed dict; its failure was that nobody stored it.

SQLite in memory with foreign keys on, matching `test_resource_interactions.py`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

USER = "rec-test-user"


@pytest.fixture
async def repo(monkeypatch):
    import src.shared.database as shared_db
    from src.domains.identity import db_models as identity_models
    from src.domains.knowledge import db_models as knowledge_models
    from src.domains.knowledge import repository as repository_module
    from src.shared.database.base import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _enforce_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    tables = [
        identity_models.User.__table__,
        knowledge_models.Resource.__table__,
    ]
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=tables)

    monkeypatch.setattr(shared_db, "get_session_factory", lambda: factory)
    monkeypatch.setattr(repository_module, "get_session_factory", lambda: factory)

    async with factory() as session:
        session.add(identity_models.User(id=USER, email="learner@example.com"))
        await session.commit()

    yield repository_module.knowledge_repo
    await engine.dispose()


@pytest.fixture
def recommend(monkeypatch):
    """`recommend_resources` with the two network calls replaced.

    Returns a configure function so each test states only the model reply and which URLs are
    reachable, rather than restating the plumbing.
    """
    from src.domains.intelligence.reasoning import llm as llm_module
    from src.domains.knowledge.services import resource_service, url_validator

    def configure(*, text: str, grounded: bool = True, reachable: dict[str, str | None]):
        async def fake_generate(prompt, **kwargs):
            return llm_module.GroundedResult(
                text=text,
                sources=(
                    [llm_module.GroundingSource(url="https://s/x", title="Search")]
                    if grounded
                    else []
                ),
            )

        async def fake_check(urls):
            return {
                url: url_validator.CheckedUrl(
                    original=url,
                    resolved=reachable.get(url),
                    status=200 if reachable.get(url) else 404,
                    reason=None if reachable.get(url) else "404",
                )
                for url in urls
            }

        # Patched where they are looked up. Both are imported inside the function body, so the
        # binding that matters is the one on the defining module.
        monkeypatch.setattr(llm_module, "generate_grounded_content", fake_generate)
        monkeypatch.setattr(url_validator, "check_urls", fake_check)

        async def fake_memory(_user_id):
            return {}

        from src.domains.intelligence.memory import memory_service

        monkeypatch.setattr(memory_service, "get_memory_context", fake_memory)
        return resource_service.recommend_resources

    return configure


def _payload(*items) -> str:
    """A model reply: prose around a JSON array, which is what grounded replies look like.

    Wrapped in commentary on purpose — grounding and structured output cannot be combined in
    this SDK, so the parser has to find the array inside prose rather than being handed clean
    JSON.
    """
    import json

    return (
        "Here are some resources I found for you:\n\n"
        + json.dumps(list(items))
        + "\n\nLet me know if you want more."
    )


# ---------------------------------------------------------------------------
# Persistence — the four columns that had never been written
# ---------------------------------------------------------------------------


async def test_a_recommendation_becomes_a_row_with_all_four_columns_set(repo, recommend):
    """The whole point. Every one of these four assertions fails against the old version,
    which persisted nothing at all."""
    run = recommend(
        text=_payload(
            {
                "title": "Dijkstra's algorithm",
                "url": "https://example.com/dijkstra",
                "description": "A walkthrough.",
                "type": "ARTICLE",
                "relevance": "Covers the exact shortest-path case you asked about.",
                "score": 0.9,
            }
        ),
        reachable={"https://example.com/dijkstra": "https://example.com/dijkstra"},
    )

    result = await run(user_id=USER, query="shortest paths", limit=5)

    assert len(result["recommendations"]) == 1
    stored = await repo.find_resource(result["recommendations"][0].id, USER)
    assert stored is not None
    assert stored.is_recommended is True
    assert stored.recommendation_score == 0.9
    assert stored.recommendation_source == "gemini_grounded"
    # The column `create_resource` did not even map, so this fails twice over on the old code.
    assert stored.recommendation_reason == (
        "Covers the exact shortest-path case you asked about."
    )


async def test_the_stored_url_is_the_resolved_one_not_the_proposed_one(repo, recommend):
    """Grounding hands back `grounding-api-redirect` indirections. Storing the proposal
    instead of its destination would put a Google redirect in the learner's library."""
    run = recommend(
        text=_payload(
            {"title": "Docs", "url": "http://python.org/doc", "type": "WEBSITE", "score": 0.8}
        ),
        reachable={"http://python.org/doc": "https://www.python.org/doc/"},
    )

    result = await run(user_id=USER, query="python docs", limit=5)

    assert result["recommendations"][0].url == "https://www.python.org/doc/"


# ---------------------------------------------------------------------------
# Link checking — what makes persisting safe
# ---------------------------------------------------------------------------


async def test_unreachable_urls_are_not_stored_and_are_counted(repo, recommend):
    run = recommend(
        text=_payload(
            {"title": "Real", "url": "https://example.com/real", "score": 0.9},
            {"title": "Invented", "url": "https://example.com/invented", "score": 0.9},
        ),
        reachable={"https://example.com/real": "https://example.com/real"},
    )

    result = await run(user_id=USER, query="graphs", limit=5)

    assert [r.title for r in result["recommendations"]] == ["Real"]
    assert result["discarded"] == 1
    # And the dead one left nothing behind.
    rows, total = await repo.list_resources(where={"userId": USER})
    assert total == 1


async def test_nothing_is_stored_when_every_url_is_dead(repo, recommend):
    """The failure mode that made persistence dangerous. A batch of invented URLs must
    produce an empty result, not a library full of 404s."""
    run = recommend(
        text=_payload(
            {"title": "A", "url": "https://example.com/a"},
            {"title": "B", "url": "https://example.com/b"},
        ),
        reachable={},
    )

    result = await run(user_id=USER, query="nonsense", limit=5)

    assert result["recommendations"] == []
    assert result["discarded"] == 2
    _, total = await repo.list_resources(where={"userId": USER})
    assert total == 0


async def test_an_item_with_no_url_is_discarded_rather_than_stored_blank(repo, recommend):
    """The old version wrote `url: ""` for these. A resource row whose URL is the empty
    string is a permanent dead entry in the library."""
    run = recommend(
        text=_payload({"title": "No link", "description": "Just a title"}),
        reachable={},
    )

    result = await run(user_id=USER, query="x", limit=5)

    assert result["recommendations"] == []
    assert result["discarded"] == 1


# ---------------------------------------------------------------------------
# Dedupe
# ---------------------------------------------------------------------------


async def test_recommending_the_same_page_twice_does_not_store_it_twice(repo, recommend):
    payload = _payload(
        {"title": "Dijkstra", "url": "https://example.com/d", "score": 0.9}
    )
    reachable = {"https://example.com/d": "https://example.com/d"}

    first = await recommend(text=payload, reachable=reachable)(
        user_id=USER, query="shortest paths", limit=5
    )
    second = await recommend(text=payload, reachable=reachable)(
        user_id=USER, query="shortest path algorithms", limit=5
    )

    # Same row returned, not a duplicate created.
    assert first["recommendations"][0].id == second["recommendations"][0].id
    _, total = await repo.list_resources(where={"userId": USER})
    assert total == 1


async def test_dedupe_is_on_the_resolved_url_not_the_title(repo, recommend):
    """A title is the model's phrasing and varies between runs; the URL is the identity of
    the thing. Deduping on title would file the same page twice whenever the wording moved."""
    reachable = {"https://example.com/d": "https://example.com/d"}
    await recommend(
        text=_payload({"title": "Dijkstra's algorithm", "url": "https://example.com/d"}),
        reachable=reachable,
    )(user_id=USER, query="a", limit=5)
    await recommend(
        text=_payload({"title": "A guide to shortest paths", "url": "https://example.com/d"}),
        reachable=reachable,
    )(user_id=USER, query="b", limit=5)

    _, total = await repo.list_resources(where={"userId": USER})
    assert total == 1


async def test_two_proposals_that_redirect_to_the_same_page_collapse(repo, recommend):
    run = recommend(
        text=_payload(
            {"title": "One", "url": "http://example.com/x"},
            {"title": "Two", "url": "https://example.com/x?utm_source=g"},
        ),
        reachable={
            "http://example.com/x": "https://example.com/x",
            "https://example.com/x?utm_source=g": "https://example.com/x",
        },
    )

    result = await run(user_id=USER, query="x", limit=5)

    assert len(result["recommendations"]) == 1
    _, total = await repo.list_resources(where={"userId": USER})
    assert total == 1


# ---------------------------------------------------------------------------
# Grounding, honestly reported
# ---------------------------------------------------------------------------


async def test_an_ungrounded_reply_still_works_but_is_marked_as_such(repo, recommend):
    """The search tool is a request, not a guarantee — Gemini decides whether to call it. An
    ungrounded reply can still produce URLs that happen to resolve, so the request is not
    failed; but the row must not claim search found them, and the caller is told."""
    run = recommend(
        text=_payload({"title": "Guess", "url": "https://example.com/g", "score": 0.7}),
        grounded=False,
        reachable={"https://example.com/g": "https://example.com/g"},
    )

    result = await run(user_id=USER, query="x", limit=5)

    assert result["grounded"] is False
    assert len(result["recommendations"]) == 1
    stored = await repo.find_resource(result["recommendations"][0].id, USER)
    assert stored.recommendation_source == "gemini"


async def test_grounded_is_true_when_search_ran(repo, recommend):
    run = recommend(
        text=_payload({"title": "Found", "url": "https://example.com/f"}),
        reachable={"https://example.com/f": "https://example.com/f"},
    )
    result = await run(user_id=USER, query="x", limit=5)
    assert result["grounded"] is True


async def test_an_unparseable_reply_yields_nothing_rather_than_raising(repo, recommend):
    """Degraded, not failed. The reply is prose because a response schema cannot be attached
    to a request carrying tools, so an unparseable answer is a normal outcome."""
    run = recommend(text="I could not find anything useful.", reachable={})

    result = await run(user_id=USER, query="x", limit=5)

    assert result["recommendations"] == []
    assert result["discarded"] == 0
    assert result["query"] == "x"


# ---------------------------------------------------------------------------
# Field coercion
# ---------------------------------------------------------------------------


async def test_limit_is_enforced_against_an_overlong_reply(repo, recommend):
    """The model is asked for `limit` items and does not always comply. Without the slice a
    request for two could store twenty rows."""
    run = recommend(
        text=_payload(*[{"title": f"R{i}", "url": f"https://example.com/{i}"} for i in range(6)]),
        reachable={f"https://example.com/{i}": f"https://example.com/{i}" for i in range(6)},
    )

    result = await run(user_id=USER, query="x", limit=2)

    assert len(result["recommendations"]) == 2


async def test_an_unrecognised_type_becomes_other(repo, recommend):
    """`type` has no database-level constraint, so a value written through unchecked becomes
    a stored string no filter chip can match.

    `INTERACTIVE_SIMULATION` is the example rather than something plausible like `PODCAST`,
    which this test used to use — `PODCAST` is in the enum and only looked unrecognised because
    the allowlist here had drifted from it. A test asserting a real kind gets downgraded is a
    test pinning the bug in place.
    """
    run = recommend(
        text=_payload(
            {"title": "T", "url": "https://example.com/t", "type": "INTERACTIVE_SIMULATION"}
        ),
        reachable={"https://example.com/t": "https://example.com/t"},
    )

    result = await run(user_id=USER, query="x", limit=5)

    assert result["recommendations"][0].type == "OTHER"


async def test_lowercase_types_are_accepted(repo, recommend):
    run = recommend(
        text=_payload({"title": "T", "url": "https://example.com/t", "type": "video"}),
        reachable={"https://example.com/t": "https://example.com/t"},
    )
    result = await run(user_id=USER, query="x", limit=5)
    assert result["recommendations"][0].type == "VIDEO"


@pytest.mark.parametrize(
    "given,expected",
    [
        (0.9, 0.9),
        # A percentage rather than a fraction, which the model returns often enough to handle.
        (90, 0.9),
        ("high", 0.5),
        (None, 0.5),
        (5000, 1.0),
        (-3, 0.0),
    ],
)
def test_score_coercion(given, expected):
    from src.domains.knowledge.services.resource_service import _coerce_score

    assert _coerce_score(given) == expected


def test_a_reply_that_is_not_a_list_of_objects_yields_nothing():
    """An array of bare strings parses as JSON but has no fields, and treating it as items
    would create rows titled "Untitled" with no URL."""
    from src.domains.knowledge.services.resource_service import _parse_recommendation_payload

    assert _parse_recommendation_payload('["just", "strings"]') == []
    assert _parse_recommendation_payload('{"not": "an array"}') == []
    assert _parse_recommendation_payload("[malformed") == []


# ---------------------------------------------------------------------------
# The filter the columns make possible
# ---------------------------------------------------------------------------


async def test_is_recommended_filter_separates_found_from_saved(repo, recommend):
    """Only usable now that the column is written. Before, it could return everything or
    nothing and nothing else."""
    from src.domains.knowledge.services import resource_service

    await recommend(
        text=_payload({"title": "Found by Maigie", "url": "https://example.com/found"}),
        reachable={"https://example.com/found": "https://example.com/found"},
    )(user_id=USER, query="x", limit=5)

    await repo.create_resource(
        {"userId": USER, "title": "Saved by me", "url": "https://example.com/mine"}
    )

    recommended = await resource_service.list_resources(user_id=USER, is_recommended=True)
    saved = await resource_service.list_resources(user_id=USER, is_recommended=False)
    everything = await resource_service.list_resources(user_id=USER)

    assert [r.title for r in recommended["items"]] == ["Found by Maigie"]
    assert [r.title for r in saved["items"]] == ["Saved by me"]
    assert everything["total"] == 2


async def test_search_actually_filters(repo):
    """A pre-existing defect found on the way past: `list_resources` built a Prisma-shaped
    `{"OR": [...]}` clause from `search` and the condition builder never read the key, so
    every search returned the unfiltered list. The request looked like it worked."""
    from src.domains.knowledge.services import resource_service

    await repo.create_resource(
        {"userId": USER, "title": "Dijkstra explained", "url": "https://example.com/a"}
    )
    await repo.create_resource(
        {"userId": USER, "title": "Baking sourdough", "url": "https://example.com/b"}
    )

    found = await resource_service.list_resources(user_id=USER, search="dijkstra")

    assert [r.title for r in found["items"]] == ["Dijkstra explained"]
    assert found["total"] == 1


# ---------------------------------------------------------------------------
# Sorting and search correctness
#
# Both bugs below returned a plausible page of resources rather than an error, which is why
# neither was noticed. Asserting on order and on membership is the only thing that catches them.
# ---------------------------------------------------------------------------


async def test_sorting_by_click_count_actually_sorts_by_click_count(repo):
    """`sortBy=clickCount` was accepted and ignored.

    `_to_attr` mapped only `createdAt`/`updatedAt`, and the caller resolves the result with
    `getattr(Resource, attr, Resource.created_at)` — so `clickCount`, which is the column name
    rather than the mapped attribute `click_count`, fell through to the default and the list came
    back newest-first. Mobile's "Most opened" and the web's "Popular" were both no-ops.

    Creation order is deliberately the **reverse** of click order. An earlier version of this test
    created them in the same order as their popularity and passed against the bug, because
    `created_at DESC` and `clickCount DESC` happened to agree — the fallback has to be made to
    disagree with the correct answer, or the assertion proves nothing.
    """
    from src.domains.knowledge.services import resource_service

    # Most-clicked created first, least-clicked created last.
    popular = await repo.create_resource(
        {"userId": USER, "title": "Everyone opens this", "url": "https://example.com/popular"}
    )
    middling = await repo.create_resource(
        {"userId": USER, "title": "Some open this", "url": "https://example.com/middling"}
    )
    await repo.create_resource(
        {"userId": USER, "title": "Nobody opens this", "url": "https://example.com/quiet"}
    )
    for _ in range(5):
        await repo.increment_resource_counter(popular.id, column="clickCount")
    await repo.increment_resource_counter(middling.id, column="clickCount")

    desc = await resource_service.list_resources(
        user_id=USER, sort_by="clickCount", sort_order="desc"
    )
    # Under the bug this is created-at descending, i.e. exactly the reverse.
    assert [r.title for r in desc["items"]] == [
        "Everyone opens this",
        "Some open this",
        "Nobody opens this",
    ]
    assert [r.click_count for r in desc["items"]] == [5, 1, 0]

    asc = await resource_service.list_resources(
        user_id=USER, sort_by="clickCount", sort_order="asc"
    )
    assert [r.click_count for r in asc["items"]] == [0, 1, 5]


async def test_search_treats_like_wildcards_as_literal_text(repo):
    """`%` and `_` are `LIKE` syntax, and the search interpolated them unescaped.

    So searching for `100%` matched every row and searching for `a_b` matched `axb`. The result
    was a full page of confident, wrong answers rather than a failure.
    """
    from src.domains.knowledge.services import resource_service

    await repo.create_resource(
        {"userId": USER, "title": "Scoring 100% on finals", "url": "https://example.com/1"}
    )
    await repo.create_resource(
        {"userId": USER, "title": "Unrelated study habits", "url": "https://example.com/2"}
    )

    # Under the bug this is `%%%` — matches everything.
    percent = await resource_service.list_resources(user_id=USER, search="100%")
    assert [r.title for r in percent["items"]] == ["Scoring 100% on finals"]

    # A term whose only match would come from `_` acting as a single-character wildcard.
    await repo.create_resource(
        {"userId": USER, "title": "axb notation", "url": "https://example.com/3"}
    )
    underscore = await resource_service.list_resources(user_id=USER, search="a_b")
    assert underscore["total"] == 0


async def test_search_matches_description_as_well_as_title(repo):
    """Both halves of the `OR`, not just the first."""
    from src.domains.knowledge.services import resource_service

    await repo.create_resource(
        {
            "userId": USER,
            "title": "Untitled",
            "url": "https://example.com/d",
            "description": "A guide to Dijkstra's algorithm",
        }
    )
    found = await resource_service.list_resources(user_id=USER, search="dijkstra")
    assert found["total"] == 1


async def test_a_zero_recommendation_score_is_stored_not_dropped(repo):
    """`if data.get("recommendationScore"):` dropped `0.0`.

    Zero is a real value on this scale — recommended, rated as weakly as possible — and storing
    NULL instead made the weakest recommendation indistinguishable from an unscored resource.
    """
    from src.domains.identity.db_models import User as UserModel
    from src.domains.knowledge.services import resource_service

    created = await resource_service.create_resource(
        user=UserModel(id=USER, email="learner@example.com"),
        data={
            "title": "Barely worth it",
            "url": "https://example.com/meh",
            "recommendationScore": 0.0,
            "recommendationReason": "Tangentially related.",
        },
    )

    assert created.recommendation_score == 0.0
    # And the fourth field is settable at all, which it was not.
    assert created.recommendation_reason == "Tangentially related."


def test_recommendable_types_covers_the_published_enum():
    """The allowlist is derived from the enum rather than restated.

    The hand-written copy had already drifted — it omitted `PODCAST`, so a correctly identified
    podcast was downgraded to `OTHER`. This fails if the two ever diverge again.
    """
    from src.domains.knowledge.models import ResourceType
    from src.domains.knowledge.services.resource_service import _RECOMMENDABLE_TYPES

    assert _RECOMMENDABLE_TYPES == {member.value for member in ResourceType}
    assert "PODCAST" in _RECOMMENDABLE_TYPES


async def test_a_podcast_recommendation_keeps_its_type(repo, recommend):
    run = recommend(
        text=_payload({"title": "A podcast", "url": "https://example.com/p", "type": "PODCAST"}),
        reachable={"https://example.com/p": "https://example.com/p"},
    )
    result = await run(user_id=USER, query="x", limit=5)
    assert result["recommendations"][0].type == "PODCAST"
