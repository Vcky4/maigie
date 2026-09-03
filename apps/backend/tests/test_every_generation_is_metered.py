"""Every generation call site passes what the meter needs, checked by walking the source.

**This is the only test in the metering set that fails for a call site nobody wrote a test for**, and
that is the point. `test_llm_metering.py` covers what the chokepoint *does* — retries are charged,
exemptions are honoured, a broken meter cannot lose a generation. None of it fails when someone adds
a 28th call site and forgets `user_id`, and a call without `user_id` is silently free: `_meter`
returns early, nothing is charged, and the surface is unmetered again with every existing test green.

That is exactly how the product arrived at 26 unmetered call sites in the first place. §6.5 is a list
of operations nobody meant to leave free; they were left free one call at a time, each time by
omission rather than by decision. A behavioural test cannot catch an omission. This walks the AST
instead, so the failure arrives on the commit that introduces it and names the file and line.

Two arguments are required of every call:

**`user_id`** — what makes a call chargeable at all. Its absence is correct only for genuinely
system-initiated work, and there is an explicit allowlist below for those, so "no learner to charge"
has to be claimed rather than defaulted into.

**`operation`** — what the charge is attributed to, *and* what Decision P reads to pick the model. An
unlabelled call is charged (the default has to be "charge") but it is charged to `"unknown"`, and it
gets the standard model regardless of tier — so a missing label is a silent hole in the paywall as
well as in the reporting.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

#: The functions that reach a provider and therefore have to be told who is paying.
METERED_HELPERS = {"generate_content", "generate_content_json"}

SRC = Path(__file__).resolve().parents[1] / "src"

#: Modules that legitimately call a generation helper without a learner attached.
#:
#: Each entry is a claim that there is nobody to charge, not that the call is cheap. Keeping it as an
#: allowlist rather than a convention means adding one is a visible decision in a diff.
NO_LEARNER_ALLOWED = {
    # The chokepoint itself. `generate_content_json` calls `generate_content` internally, threading
    # through whatever the caller passed.
    "domains/personal_learning/services/llm_resilient.py",
}

#: Modules allowed to reach a provider SDK directly, bypassing the chokepoint entirely.
#:
#: **This is the hole the `user_id` check cannot see.** A module that never calls the helper is not
#: an unlabelled call site — it is an invisible one, and it was the shape of the original problem:
#: `memory_impl`, `planning_impl` and `schedule_regen_impl` each built their own client, and none of
#: them appeared in any survey of metered call sites because they were not call sites.
#:
#: Two entries, each a recorded decision rather than an oversight. See the commercial plan's
#: Decision L for the first and Decision F for the second.
SDK_BYPASS_ALLOWED = {
    # The provider adapters. These *are* the layer the SDK belongs to.
    "domains/intelligence/reasoning/llm",
    # Owns a 429 path that raises `PlanRateLimited` so a route can say "AI is temporarily busy"
    # rather than failing; the chokepoint would swallow it into a generic `LLMUnavailableError`. It
    # meters itself through `meter_usage`, and at ~225 units it is below Decision P's threshold so
    # the tier gate it forgoes is a no-op.
    "domains/intelligence/planning/planning_impl.py",
    # Space-scoped, out of scope by Decision F. Draws on the Space pool rather than a learner's
    # window, and the plan is explicit that it stays unmetered.
    "domains/learning_spaces/services/space_impl.py",
}

#: How the Gemini SDK's generate method is reached: `client.aio.models.generate_content(...)`.
SDK_CALL_ATTR = "generate_content"


def _iter_calls():
    """Yield `(relative_path, lineno, func_name, kwargs)` for every metered-helper call in `src`."""
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(SRC).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a syntax error is another test's problem
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # **The Gemini SDK's own method is called `generate_content` too**, and matching on the
            # bare name flagged `client.aio.models.generate_content(...)` in the provider adapters as
            # an unmetered call site. It is not one — it is the thing our helper wraps, and it takes
            # no `user_id` because there is no meter at that layer.
            #
            # Distinguished structurally rather than by an allowlist: our helpers are called bare
            # (`generate_content(...)`) or on a module alias (`llm_resilient.generate_content(...)`),
            # so the attribute's value is a `Name`. The SDK form is a nested chain, so its value is
            # an `Attribute`. That keeps the check honest as adapters are added, where a
            # path-based exclusion would have quietly stopped covering new ones.
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                name = func.attr
            else:
                continue
            if name not in METERED_HELPERS:
                continue
            # A definition's own recursive call, or the helper calling its sibling, is plumbing.
            kwargs = {kw.arg for kw in node.keywords if kw.arg}
            yield rel, node.lineno, name, kwargs


def _sites():
    return [
        (rel, lineno, name, kwargs)
        for rel, lineno, name, kwargs in _iter_calls()
        if rel not in NO_LEARNER_ALLOWED
    ]


class TestTheWalkItselfWorks:
    """A source-walking test that finds nothing passes for the wrong reason."""

    def test_it_finds_the_call_sites(self):
        sites = _sites()
        assert len(sites) >= 15, (
            f"expected the generation call sites to be found, got {len(sites)} — "
            "if the helpers were renamed, METERED_HELPERS needs updating or this test is asleep"
        )


class TestEveryCallSitePaysSomeone:
    def test_every_call_passes_user_id(self):
        missing = [
            f"{rel}:{lineno} -> {name}()"
            for rel, lineno, name, kwargs in _sites()
            if "user_id" not in kwargs
        ]
        assert not missing, (
            "these generation calls pass no `user_id`, so nothing is charged for them:\n  "
            + "\n  ".join(missing)
            + "\n\nA call without `user_id` is silently free. If the work is genuinely "
            "system-initiated, add the module to NO_LEARNER_ALLOWED with a reason."
        )

    def test_every_call_passes_an_operation(self):
        missing = [
            f"{rel}:{lineno} -> {name}()"
            for rel, lineno, name, kwargs in _sites()
            if "operation" not in kwargs
        ]
        assert not missing, (
            "these generation calls pass no `operation`, so the charge is attributed to "
            '"unknown" and Decision P cannot pick a model by tier:\n  ' + "\n  ".join(missing)
        )


def _sdk_callers() -> set[str]:
    """Modules reaching a provider SDK directly, found by the nested-attribute call shape."""
    found: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(SRC).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == SDK_CALL_ATTR
                and isinstance(node.func.value, ast.Attribute)
            ):
                found.add(rel)
    return found


def _allowed(rel: str, allowlist: set[str]) -> bool:
    return any(rel == a or rel.startswith(f"{a}/") for a in allowlist)


class TestNothingBypassesTheChokepoint:
    """The guard the `user_id` check cannot provide, and the one that matches the original problem.

    A module that builds its own client is not an unlabelled call site, it is an **invisible** one.
    Three modules did exactly that — `memory_impl`, `planning_impl`, `schedule_regen_impl` — and none
    appeared in any survey of metered call sites, because they were not call sites. The cost was
    unattributable rather than merely unattributed, which is a harder thing to notice.
    """

    def test_only_recorded_exceptions_reach_a_provider_sdk(self):
        offenders = sorted(rel for rel in _sdk_callers() if not _allowed(rel, SDK_BYPASS_ALLOWED))
        assert not offenders, (
            "these modules reach a provider SDK directly and so bypass the meter, the headroom "
            "gate, the retry, the tier and the thinking budget:\n  "
            + "\n  ".join(offenders)
            + "\n\nRoute them through `llm_resilient`, or add them to SDK_BYPASS_ALLOWED with a "
            "reason — as Decision L required of the three that used to be here."
        )

    def test_sdk_bypass_entries_still_reach_an_sdk(self):
        """A spent exception should go, or the next reader takes it for a standing one."""
        callers = _sdk_callers()
        stale = sorted(
            a for a in SDK_BYPASS_ALLOWED if not any(_allowed(rel, {a}) for rel in callers)
        )
        assert not stale, f"SDK_BYPASS_ALLOWED entries that no longer reach an SDK: {stale}"

    def test_every_allowlisted_path_exists(self):
        for rel in sorted(NO_LEARNER_ALLOWED | SDK_BYPASS_ALLOWED):
            assert (SRC / rel).exists(), f"allowlist names a missing path: {rel}"


class TestTheAllowlistIsHonest:
    def test_every_allowlisted_module_exists(self):
        """An allowlist entry for a moved or deleted file is an exemption nobody can see. It would
        keep passing while the real module — under its new path — went unchecked."""
        for rel in sorted(NO_LEARNER_ALLOWED):
            assert (SRC / rel).exists(), f"NO_LEARNER_ALLOWED names a missing module: {rel}"

    def test_every_allowlisted_module_actually_calls_a_helper(self):
        """An entry that no longer needs to be there should be removed, or the next person reads it
        as a standing exception rather than a spent one."""
        callers = {rel for rel, _lineno, _name, _kwargs in _iter_calls()}
        stale = sorted(rel for rel in NO_LEARNER_ALLOWED if rel not in callers)
        assert not stale, f"NO_LEARNER_ALLOWED entries that no longer call a helper: {stale}"
