"""The domain event bus: that it is connected at all, and that it stays connected.

Ten handlers existed across two modules and **not one of them had ever run**. Three separate faults, each
sufficient on its own:

1. `@listen` registers on import, and nothing imported the handler modules. `import src.app` left the
   registry empty; the only importers did it function-locally inside a request path, so whether a handler
   fired depended on which unrelated code path a given process had happened to run first.
2. The handlers that write something listened for names **nothing emits**: `progress.streak_updated`,
   `progress.achievement_unlocked`, `knowledge.topic_completed` (the emitted name is `topic.completed`).
3. `emit` returns silently when nothing is listening, so there was no signal at any level above debug.

Thirty event names are emitted in this codebase and twenty-five have no listener. That is not necessarily
wrong — an event with no subscriber yet is a reasonable thing — but a *listener* with no emitter is always
a bug, and this file is what makes it a failing test instead of a comment.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from src.shared.events import bus
from src.shared.events.registry import HANDLER_MODULES, register_handlers

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"


def _constant_values() -> dict[tuple[str, str], str]:
    """`{(ClassName, ATTR): "event.name"}` for every constant in `shared/events/types`."""
    from src.shared.events import types

    values: dict[tuple[str, str], str] = {}
    for class_name in dir(types):
        cls = getattr(types, class_name)
        if not (isinstance(cls, type) and class_name.endswith("Events")):
            continue
        for attr, value in vars(cls).items():
            if not attr.startswith("_") and isinstance(value, str):
                values[(class_name, attr)] = value
    return values


def _call_sites() -> list[tuple[str, str, str, int]]:
    """Every `emit` / `listen` call in the tree as `(kind, module, resolved_name_or_None, lineno)`.

    Static rather than runtime, deliberately: a runtime check can only see what the current process
    imported, which is the exact blind spot that let this rot. Every call site in the tree counts,
    whether or not anything imported it.

    A first argument written as `Class.ATTR` is resolved through `types`; a raw string literal resolves
    to itself; anything else resolves to `None` so the "must use a constant" test can name it.
    """
    constants = _constant_values()
    sites: list[tuple[str, str, str, int]] = []

    for path in SRC.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (
            SyntaxError
        ):  # pragma: no cover - a file that does not parse is another test's problem
            continue

        dotted = ".".join(path.relative_to(SRC.parent).with_suffix("").parts)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id not in {"emit", "listen"} or not node.args:
                continue

            first = node.args[0]
            resolved: str | None = None
            if isinstance(first, ast.Attribute) and isinstance(first.value, ast.Name):
                resolved = constants.get((first.value.id, first.attr))
            elif isinstance(first, ast.Constant) and isinstance(first.value, str):
                # A literal. Resolves to itself so the pairing checks still work, and the
                # `test_no_call_site_uses_a_raw_string` below is what refuses it.
                resolved = first.value

            sites.append((node.func.id, dotted, resolved, node.lineno))

    return sites


def _event_names() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """`(emitted, listened)`, each `{event_name: [module dotted paths]}`."""
    emitted: dict[str, list[str]] = {}
    listened: dict[str, list[str]] = {}

    for kind, module, name, _lineno in _call_sites():
        if name is None:
            continue
        target = emitted if kind == "emit" else listened
        target.setdefault(name, []).append(module)

    return emitted, listened


#: Listeners kept despite having no emitter, each with the reason it cannot be wired yet.
#:
#: An entry here is a promise that the gap is understood, not that it is acceptable. Anything not listed
#: fails the test below, which is what stops a fourth handler being added for an event nobody sends.
LISTENERS_WITHOUT_AN_EMITTER: dict[str, str] = {
    "progress.achievement_unlocked": (
        "Nothing awards achievements. `progress_repo.create_achievement` is called from nowhere in "
        "`src` — the four live rows were inserted by hand — so there is no honest place to emit this. "
        "The handler is correct and waiting for the feature, and wiring an emit would mean inventing "
        "an award rule. Recorded in `reflect_dashboard_service` and `reflect_aggregates` as well."
    ),
    "classroom.session_ended": (
        "No emitter, and the handler body is a `logger.debug` — the comment above it describes "
        "connection entries for the home service that were never written. Scaffolding, kept because "
        "deleting a domain's stated intention is a product decision, not a cleanup."
    ),
    "classroom.discussion_created": (
        "Same as `classroom.session_ended`: no emitter, and the handler only logs."
    ),
}


#: Events emitted with nothing subscribed, and why each is acceptable.
#:
#: An event with no subscriber is legitimate — publishing a fact and letting consumers appear later is
#: the point of a bus. An *unnoticed* one is not, which is the difference this inventory makes: adding a
#: fire-and-forget emit costs one line here, and wiring a listener means deleting one. Twenty-five of
#: these existed before anybody counted them.
#:
#: Two distinct situations are recorded, because they are not equally harmless:
#:
#: - `fires` — the emit runs on a real code path and lands nowhere. A signal waiting for a consumer.
#: - `unreachable` — the emit sits inside a wrapper function that **nothing calls**, so it never runs at
#:   all. The event name is declared, the helper is written, and no code reaches it. That is dead code
#:   wearing the shape of a feature, and it is how `create_schedule_block_for_review` and
#:   `update_progress` read too.
EMITTERS_WITHOUT_A_LISTENER: dict[str, tuple[str, str]] = {
    # --- fire from real code paths ---
    #
    # `billing.credits_purchased` used to be listed here. Its only emitter was
    # `credit_service.claim_ad_reward`, and rewarded ads are withdrawn: nothing in the
    # product asks a learner to watch an advertisement. `BillingEvents.CREDITS_PURCHASED`
    # survives on the enum because a purchase event is coming back — a Plus pass purchase,
    # which is a fact worth publishing — but nothing emits it today and the inventory only
    # describes what does.
    "billing.subscription_cancelled": (
        "fires",
        "Emitted inline by `subscription_service`.",
    ),
    "billing.referral_linked": (
        "fires",
        "Emitted inline by `identity.services` when a referral lands.",
    ),
    "classroom.created": ("fires", "Emitted inline by `classroom_service`."),
    "classroom.session_started": ("fires", "Emitted inline by `session_service`."),
    "space.created": ("fires", "Emitted by `space_service` as well as the wrapper."),
    "space.member_left": ("fires", "Emitted by `membership_service` in two places."),
    "space.role_changed": ("fires", "Emitted by `membership_service`."),
    "resource.added": ("fires", "Emitted from two call sites in the knowledge domain."),
    "topic.uncompleted": (
        "fires",
        "Emitted whenever a learner reopens a topic. Deliberately unhandled: completing a topic now "
        "opens a spaced-repetition schedule, and the tempting symmetry — delete the review when the "
        "topic reopens — would throw away the SM-2 history (ease factor, repetitions, lapses) that "
        "makes the schedule worth anything. Reviewing a topic you are restudying is not a defect, and "
        "`create_review_item_for_topic` is idempotent, so re-completing does not duplicate or reset it.",
    ),
    "user.verified": ("fires", "Emitted on email verification from two call sites."),
    "personal_learning.milestone_reached": (
        "fires",
        "Now genuinely emitted: `handle_streak_updated` publishes it after celebrating a streak "
        "milestone. Nothing subscribes yet, which is the honest state — a milestone feed would be a "
        "product decision, not a wiring one.",
    ),
    # --- the emit is inside a wrapper nothing calls ---
    "course.completed": (
        "unreachable",
        "`emit_course_completed` has no callers. Nothing marks a course complete as an event, though "
        "`Course.progress` reaching 100 is derivable — see the goal derivation work in §9.11.",
    ),
    "user.onboarded": ("unreachable", "`emit_user_onboarded` has no callers."),
    "user.tier_changed": ("unreachable", "`emit_user_tier_changed` has no callers."),
    "user.deletion_requested": (
        "unreachable",
        "`emit_deletion_requested` has no callers.",
    ),
    "user.deletion_cancelled": (
        "unreachable",
        "`emit_deletion_cancelled` has no callers.",
    ),
    "personal_learning.note_created": ("unreachable", "Wrapper has no callers."),
    "personal_learning.topic_studied": ("unreachable", "Wrapper has no callers."),
    "personal_learning.topic_completed": (
        "unreachable",
        "Wrapper has no callers. Its one caller was `POST /learning/.../topics/{id}/complete`, which "
        "announced this instead of completing anything; that route delegates to the knowledge service "
        "now and the knowledge domain's `topic.completed` is the name two listeners act on.",
    ),
    "personal_learning.quiz_completed": ("unreachable", "Wrapper has no callers."),
    "personal_learning.study_session_ended": ("unreachable", "Wrapper has no callers."),
    "personal_learning.flashcard_reviewed": ("unreachable", "Wrapper has no callers."),
    "personal_learning.preparation_completed": (
        "unreachable",
        "Wrapper has no callers.",
    ),
    "personal_learning.study_plan_item_completed": (
        "unreachable",
        "Wrapper has no callers.",
    ),
}


class TestTheEmitterInventoryIsHonest:
    def test_every_unheard_event_is_listed(self):
        emitted, listened = _event_names()

        unheard = {name for name in emitted if name not in listened}
        missing = unheard - set(EMITTERS_WITHOUT_A_LISTENER)
        assert not missing, (
            "These events are emitted with nothing listening and are not recorded. Add a listener, or "
            f"add a line to EMITTERS_WITHOUT_A_LISTENER saying why not: {sorted(missing)}"
        )

    def test_the_inventory_does_not_outlive_its_entries(self):
        """A listed event that now has a listener should leave the list, or the next reader trusts a
        stale description of the system."""
        emitted, listened = _event_names()

        resolved = sorted(name for name in EMITTERS_WITHOUT_A_LISTENER if name in listened)
        assert not resolved, f"These have listeners now and should be removed from EMITTERS_WITHOUT_A_LISTENER: {resolved}"

    def test_the_inventory_only_names_emitted_events(self):
        emitted, _listened = _event_names()

        orphans = sorted(name for name in EMITTERS_WITHOUT_A_LISTENER if name not in emitted)
        assert not orphans, f"EMITTERS_WITHOUT_A_LISTENER names events nothing emits: {orphans}"

    @pytest.mark.parametrize("name", sorted(EMITTERS_WITHOUT_A_LISTENER))
    def test_each_entry_states_a_category_and_a_reason(self, name):
        category, reason = EMITTERS_WITHOUT_A_LISTENER[name]

        assert category in {
            "fires",
            "unreachable",
        }, f"{name}: unknown category {category!r}"
        assert len(reason) > 20, f"{name}: the reason is not a reason"


class TestTheRegistryCoversEveryHandler:
    """The test that would have caught the original fault.

    A `@listen` in a module absent from `HANDLER_MODULES` is a handler that will never run, and nothing
    else in the system says so — not a type error, not a failing import, not a log line.
    """

    def test_every_module_holding_a_listener_is_registered(self):
        _emitted, listened = _event_names()
        modules_with_handlers = {module for modules in listened.values() for module in modules}

        missing = modules_with_handlers - set(HANDLER_MODULES)
        assert not missing, (
            f"These modules define @listen handlers but are not in HANDLER_MODULES, so their handlers "
            f"never register: {sorted(missing)}"
        )

    def test_every_registered_module_actually_holds_a_listener(self):
        """The other direction, so the tuple does not accumulate modules that no longer listen."""
        _emitted, listened = _event_names()
        modules_with_handlers = {module for modules in listened.values() for module in modules}

        stale = set(HANDLER_MODULES) - modules_with_handlers
        assert not stale, f"HANDLER_MODULES lists modules with no @listen handler: {sorted(stale)}"


class TestEveryListenerHasSomethingToListenTo:
    def test_no_listener_waits_for_an_event_nobody_emits(self):
        """`knowledge.topic_completed` was the live instance: the emitted name is `topic.completed`, so
        that handler could not have fired even with registration fixed."""
        emitted, listened = _event_names()

        unheard = {
            name: modules
            for name, modules in listened.items()
            if name not in emitted and name not in LISTENERS_WITHOUT_AN_EMITTER
        }
        assert not unheard, (
            "These handlers listen for events nothing emits. Either emit the event, correct the name, "
            f"or record why in LISTENERS_WITHOUT_AN_EMITTER: {unheard}"
        )

    def test_the_allowlist_does_not_outlive_its_reason(self):
        """An allowlisted name that *is* now emitted should leave the list, or the next reader will
        believe a working path is broken."""
        emitted, _listened = _event_names()

        resolved = sorted(set(LISTENERS_WITHOUT_AN_EMITTER) & set(emitted))
        assert not resolved, (
            f"These names are emitted now and should be removed from LISTENERS_WITHOUT_AN_EMITTER: "
            f"{resolved}"
        )

    def test_the_allowlist_only_covers_names_something_listens_for(self):
        _emitted, listened = _event_names()

        orphans = sorted(set(LISTENERS_WITHOUT_AN_EMITTER) - set(listened))
        assert not orphans, f"LISTENERS_WITHOUT_AN_EMITTER names nothing listens for: {orphans}"


class TestEventNamesComeFromOneList:
    """The durable fix for the fault that cost ten handlers.

    `shared/events/types` says "Domains reference these constants rather than raw strings" and, until
    now, **nothing did** — all forty-seven call sites passed literals and the classes were referenced
    nowhere. Two strings written months apart in different files disagreed by one word
    (`knowledge.topic_completed` against `topic.completed`) and the result was a handler that could
    never fire, with nothing to notice it.

    With constants, that mistake is an `AttributeError` at import time instead.
    """

    def test_no_call_site_uses_a_raw_string(self):
        literals = [
            (kind, module, name, lineno)
            for kind, module, name, lineno in _call_sites()
            if name is not None and name not in _constant_values().values()
        ]
        assert not literals, (
            "These emit/listen sites pass a raw string. Add the name to shared/events/types and use "
            f"the constant, so a typo cannot become a handler that waits forever: {literals}"
        )

    def test_every_call_site_resolves_to_a_known_constant(self):
        """Catches `SomeEvents.MISPELLED`, which resolves to nothing."""
        unresolved = [
            (kind, module, lineno) for kind, module, name, lineno in _call_sites() if name is None
        ]
        assert not unresolved, (
            f"These emit/listen sites name something that is not a constant in shared/events/types: "
            f"{unresolved}"
        )

    def test_no_two_constants_share_a_value(self):
        """Two names for one event would let a listener and an emitter look paired and not be."""
        values = _constant_values()
        seen: dict[str, tuple[str, str]] = {}
        duplicates: list[tuple[str, tuple[str, str], tuple[str, str]]] = []

        for key, value in values.items():
            if value in seen:
                duplicates.append((value, seen[value], key))
            else:
                seen[value] = key

        assert not duplicates, f"Duplicate event values: {duplicates}"

    def test_the_call_sites_are_all_still_found(self):
        """A guard on the guard. If the resolver stops recognising the call shape, every check above
        passes vacuously — which is exactly how a census test becomes decoration."""
        sites = _call_sites()

        assert (
            len(sites) >= 45
        ), f"only found {len(sites)} emit/listen sites; the resolver has drifted"
        assert any(kind == "emit" for kind, _m, _n, _l in sites)
        assert any(kind == "listen" for kind, _m, _n, _l in sites)


class TestRegistration:
    """No test here calls `bus.clear_handlers()`.

    It would not prove anything if it did: `register_handlers` imports modules, and after the first
    import a second `import_module` is a `sys.modules` hit, so the decorators never run again and the
    registry would stay empty. That is fine in production — registration happens once per process at
    startup and nothing clears it — but it means "clear, re-register, assert" tests what Python's import
    cache does, not what this code does, and passes or fails depending on which test ran first.

    So these assert against the source tree instead, which is order-independent.
    """

    def test_every_listener_in_the_tree_is_registered_at_runtime(self):
        _emitted, listened = _event_names()
        register_handlers()

        for name in listened:
            assert (
                bus.get_handler_count(name) > 0
            ), f"`{name}` has a @listen in the source but no handler registered at runtime"

    def test_the_runtime_count_matches_the_source(self):
        """Ties the two together, so a handler that is registered twice — or a module imported for a
        side effect somewhere else as well — shows up as a mismatch rather than a duplicate dispatch.
        """
        _emitted, listened = _event_names()
        register_handlers()

        expected = sum(len(modules) for modules in listened.values())
        assert bus.get_handler_count() == expected

    def test_registering_twice_does_not_double_up(self):
        """Both the web app and the Celery app register, and one process can be both."""
        first = register_handlers()
        second = register_handlers()

        assert first == second

    def test_the_web_app_registers_on_startup(self):
        """A source guard: the call is in `lifespan`, which is the only place that runs in every web
        process regardless of which routes are hit."""
        import inspect

        from src import app as app_module

        assert "register_handlers" in inspect.getsource(app_module.lifespan)

    def test_the_celery_app_registers_too(self):
        """A worker that never imported a handler module dispatches nothing, and workers emit events."""
        import pathlib as _pathlib

        source = (SRC / "core" / "celery_app.py").read_text(encoding="utf-8-sig")
        assert "register_handlers" in source
        assert _pathlib.Path(SRC / "core" / "celery_app.py").exists()


class TestTopicCompletionIsActedOn:
    """`topic.completed` is emitted whenever a learner finishes a topic, and until now the only handler
    logged at debug. Three now: a review schedule, a flashcard suggestion, and the observer.
    """

    def test_three_handlers_are_registered_for_it(self):
        register_handlers()

        names = {handler.__name__ for handler in bus._handlers["topic.completed"]}
        assert "schedule_first_review" in names
        assert "handle_knowledge_topic_completed" in names

    async def test_a_completed_topic_opens_a_review_schedule(self):
        from unittest.mock import patch

        from src.domains.progress import listeners

        calls: list[tuple[str, str]] = []

        async def _create(user_id, topic_id):
            calls.append((user_id, topic_id))
            return type("Review", (), {"next_review_at": None})()

        with patch(
            "src.domains.progress.services.spaced_repetition_impl.create_review_item_for_topic",
            _create,
        ):
            await listeners.schedule_first_review({"user_id": "u1", "topic_id": "t1"})

        assert calls == [("u1", "t1")]

    async def test_it_does_not_materialise_a_schedule_block(self):
        """`ensure_review_item_for_completed_topic` also writes a block, and the agenda composes due
        reviews on read — a block would be a second record of one commitment, needing to be found and
        rewritten every time SM-2 moves the due date."""
        source = _code_only(_listener_source())

        assert "ensure_review_item_for_completed_topic" not in source
        assert "create_schedule_block_for_review" not in source

    async def test_a_payload_missing_its_ids_is_ignored(self):
        from src.domains.progress import listeners

        # Returns rather than raising, and writes nothing.
        assert await listeners.schedule_first_review({}) is None
        assert await listeners.schedule_first_review({"user_id": "u1"}) is None

    async def test_a_failure_is_logged_and_contained(self):
        from unittest.mock import patch

        from src.domains.progress import listeners

        async def _boom(user_id, topic_id):
            raise RuntimeError("no database")

        with patch(
            "src.domains.progress.services.spaced_repetition_impl.create_review_item_for_topic",
            _boom,
        ):
            assert (
                await listeners.schedule_first_review({"user_id": "u1", "topic_id": "t1"}) is None
            )


def _listener_source() -> str:
    import inspect

    from src.domains.progress import listeners

    return inspect.getsource(listeners.schedule_first_review)


def _code_only(source: str) -> str:
    """Source with its docstring and comments stripped.

    Both name the avoided functions in order to explain why they are avoided, so a guard that reads the
    raw source matches its own reasoning. This is the sixth such guard in this codebase.
    """
    import textwrap

    tree = ast.parse(textwrap.dedent(source))
    node = tree.body[0]
    body = getattr(node, "body", [])
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        node.body = body[1:]  # type: ignore[attr-defined]
    return ast.unparse(tree)


class TestStreakMilestones:
    """`progress.streak_updated` had no emitter, so no learner has ever been congratulated on a streak."""

    @pytest.mark.parametrize("streak", [7, 14, 30, 60, 100, 365])
    async def test_a_milestone_creates_a_celebration(self, streak):
        from unittest.mock import patch

        from src.domains.personal_learning import events

        created: list[dict] = []

        async def _create(**kwargs):
            created.append(kwargs)

        with patch(
            "src.domains.personal_learning.services.notification_service.create_notification",
            _create,
        ):
            await events.handle_streak_updated({"user_id": "u1", "streak_count": streak})

        assert len(created) == 1
        assert str(streak) in created[0]["title"]

    @pytest.mark.parametrize("streak", [1, 2, 6, 8, 29, 101])
    async def test_an_ordinary_day_creates_nothing(self, streak):
        """Every day would be five notifications a week the learner did not ask for."""
        from unittest.mock import patch

        from src.domains.personal_learning import events

        created: list[dict] = []

        async def _create(**kwargs):
            created.append(kwargs)

        with patch(
            "src.domains.personal_learning.services.notification_service.create_notification",
            _create,
        ):
            await events.handle_streak_updated({"user_id": "u1", "streak_count": streak})

        assert created == []

    async def test_a_payload_without_a_user_is_ignored(self):
        from src.domains.personal_learning import events

        assert await events.handle_streak_updated({"streak_count": 7}) is None


class TestTheStreakEmit:
    """The emit side: it must fire when the streak moves and stay quiet when it does not."""

    async def test_studying_again_the_same_day_announces_nothing(self):
        """`_update_streak` returns `None` when today was already counted, and a learner studying twice
        in a day is not a new streak day."""
        from unittest.mock import patch

        from src.domains.progress.services import activity_tracker

        emitted: list[tuple] = []

        async def _emit(name, payload):
            emitted.append((name, payload))

        async def _unchanged(user_id, today):
            return None

        with (
            patch.object(activity_tracker, "_update_streak", _unchanged),
            patch("src.shared.events.emit", _emit),
        ):
            await activity_tracker.record_activity("u1")

        assert emitted == []

    async def test_a_new_streak_day_announces_the_count(self):
        from unittest.mock import patch

        from src.domains.progress.services import activity_tracker

        emitted: list[tuple] = []

        async def _emit(name, payload):
            emitted.append((name, payload))

        async def _incremented(user_id, today):
            return 7

        with (
            patch.object(activity_tracker, "_update_streak", _incremented),
            patch("src.shared.events.emit", _emit),
        ):
            await activity_tracker.record_activity("u1")

        assert emitted == [("progress.streak_updated", {"user_id": "u1", "streak_count": 7})]

    async def test_a_failed_streak_update_announces_nothing(self):
        """Nothing changed, so there is nothing to announce — and the failure must not be reported as a
        dispatch problem."""
        from unittest.mock import patch

        from src.domains.progress.services import activity_tracker

        emitted: list[tuple] = []

        async def _emit(name, payload):
            emitted.append((name, payload))

        async def _boom(user_id, today):
            raise RuntimeError("no database")

        with (
            patch.object(activity_tracker, "_update_streak", _boom),
            patch("src.shared.events.emit", _emit),
        ):
            await activity_tracker.record_activity("u1")

        assert emitted == []


class TestCompletingATopicRecordsIt:
    def test_the_route_delegates_to_the_service_that_owns_completion(self):
        """The body used to be an `emit` and a `return`: it reported success, left `completed = false`,
        and never recounted `Course.progress`."""
        import inspect

        from src.domains.personal_learning import routes

        source = inspect.getsource(routes.complete_topic)
        assert "toggle_topic_completion" in source

    def test_it_no_longer_emits_a_name_nothing_listens_for(self):
        import inspect

        from src.domains.personal_learning import routes

        source = _code_only(inspect.getsource(routes.complete_topic))
        assert "emit_topic_completed" not in source
