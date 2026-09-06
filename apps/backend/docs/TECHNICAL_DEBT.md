# Technical debt: what was paid down, and what is left

Last updated 2026-08-09. Every claim here was checked against the code, the test suite, or
the live database, and the check is named so it can be repeated. Where something is a
judgement call or needs a product decision, it says so.

Headline numbers, start of the pass to now:

| | Before | Now |
|---|---|---|
| Modules that could not be imported | 11 | 0 |
| Stub functions | 40 | 22 |
| `ruff` findings (`src` + `tests`) | 556 | 0 |
| Tests passing | 465 | 1087 |
| Test files skipped at collection | 24 | 20 |
| Broken Celery tasks | 3 | 0 |
| Applied migration | 012 | 013 |

Scope note: sections 1–11 cover `apps/backend`, which is where the pass concentrated.
Section 12 records what a first look outside it found, and that ground is not yet fully
covered.

---

## 1. The finding that reframed the rest

**Eleven modules could not be imported at all.** Not "were incomplete" — `import` raised.

The application still started, because every one is reached lazily: either the import sits
inside a request handler, or the module is only pulled in on demand. So each failure
surfaced the first time a real user touched the feature, and never in CI.

What was broken: all three payment providers (`stripe_service`, `paystack_service`,
`google_play_service`), `referral_rewards_service`, both credit services,
`course_delete_service`, `note_impl` and the ~10 notes endpoints behind it, the chat
websocket handler, and the LLM stubs.

The mechanical cause was a domain reorganisation that moved files without updating the
paths they import, against packages that were never created: `src.utils`, `src.models`,
`src.services`, `src.domains.billing.config`, `src.domains.billing.core`,
`src.schemas.subscription`. `ResourceNotFoundError` had also been renamed to
`NotFoundError`, and `reset_credits_for_period_start` had moved from `credit_service` to
`credit_consumption_service`.

### Two guards now cover this

`tests/test_module_imports.py` walks `pkgutil.walk_packages` and asserts all 242 modules
import. Importing a module proves only that its imports resolve, which is exactly the class
of breakage that slipped through, and it costs about five seconds.

`tests/test_local_imports.py` closes the blind spot that guard had: it parses every function
body, finds each `from src.… import …` nested inside one, and asserts both that the module
exists and that it provides the names asked of it. 223 such imports exist and all resolve.
This is not hypothetical — it is what hid the three broken Celery tasks in §4, and the
`reset_credits_for_period_start` move was a case where the *symbol* was missing rather than
the module.

---

## 2. Silent failures that were fixed

Each of these accepted a call, returned a plausible value, and did nothing.

**Email** was `pass` throughout, so space invitations and credit-limit notices were accepted
and discarded. Restored with the SMTP-then-Resend fallback chain ordered by
`EMAIL_OUTBOUND_STRATEGY`, so a provider quota failure falls through instead of dropping the
message. `fastapi_mail` was dropped: it supplied three booleans and the sending was always
stdlib `smtplib`. Sender and frontend URL now resolve per send rather than at import, and
autoescaping is HTML-only so plaintext parts do not gain entities. A latent bug was fixed in
passing: `send_space_invite_email` was declared `(to_email, space_name, inviter_name)` while
its only caller passes `(email, inviter_name, space_name)` positionally, so any
implementation written against the stub would have addressed the space by the inviter's name.
Templates `circle_invite.*` became `space_invite.*`.

**Push notifications** were `pass`. Restored onto SQLAlchemy, with two changes: dead FCM
tokens are deleted rather than flagged, because `DeviceToken` has no `isActive` column and an
`UNREGISTERED` token is permanently dead; and `messaging.send_each` now runs via
`asyncio.to_thread`, since it performs blocking HTTP and the original stalled the event loop
for a whole fan-out. It still cannot deliver, for a reason outside the module: **nothing
writes `DeviceToken` rows.** There is no registration endpoint, so every send returns
`no_tokens`, reported honestly. Mobile is out of scope, which is why that surface is absent.

**Cost and revenue** returned `0.0` for every call, so cost tracking, revenue attribution and
margin reporting all read zero regardless of usage — worse than absent data, because the
numbers look valid. Ported intact, and independently confirmed: `tests/test_cost_calculator.py`,
written against the original implementation, was repointed and passes unmodified.

**The admin audit trail** was `pass`, and its signature discarded the arguments. The stub was
`log_admin_action(action, admin_id="", **kwargs)` while the caller passes `admin_user_id`,
`resource_type`, `resource_id` and `details` by keyword, so all four were absorbed by
`**kwargs`. An administrator adjusting a credit balance left no record. The `AuditLog` table
already existed (7 columns, 0 rows) with no SQLAlchemy model, so this needed a model and
**no migration**; it is mapped to the live shape including all five real indexes, so an
autogenerate diff stays empty. One inconsistency is mirrored rather than silently corrected:
`adminUserId` is `NOT NULL` but its FK is `ON DELETE SET NULL`, so deleting an administrator
would violate it. Fixing that needs a migration.

**AI usage records** were `pass`, and the stub required a `scope` argument the caller never
passed, so every call raised `TypeError` into a bare `except Exception: pass`. The
`AiUsageRecord` table and model already matched the caller's fields one for one, so this also
needed no migration.

**Websocket event publishing** was `pass`, so a user whose credit purchase completed saw
nothing until they reloaded. It now delegates to the connection registry, adding only the
envelope shape so publishers do not each invent one.

**Google Calendar sync** was three no-ops (`return None`, `return {}`, `pass`). The OAuth
flow stores Calendar tokens and sets `googleCalendarSyncEnabled`, so a user could connect
their calendar, be told it worked, and never see an event appear. Restored against the
Calendar REST API with `httpx`, including token refresh, calendar creation, and create-or-
update per block with a fallback to create when Google reports the event gone. `check_freebusy`
and `has_conflict` were deliberately not carried over: nothing calls them, and restoring them
would be adding dead code.

### Two stubs were shadowing working implementations

`observation/tracker.record_activity` was `pass`, so sending a chat message never counted
towards a study streak, while the analytics path called the real
`progress/services/activity_tracker.record_activity` directly. It now delegates.

`shared/infrastructure/socket_manager` defined a **second** `ConnectionManager` whose methods
were `pass`. A full one already exists in `src/core/websocket.py`, and the course-generation
service was already using it. **Two managers means two registries**, so a connection accepted
through one is invisible to the other — which is exactly why chat messages went nowhere while
course-progress updates arrived. It now re-exports the single instance, with `send_json`
(payload-first) added to that manager as the alias ~20 chat call sites use. Also fixed:
`manager.disconnect(...)` was called without `await` in two places, so connections were never
removed from the registry.

### The LLM stubs

`get_llm_router()` returned `None` and callers immediately call `.route_request()` on it; it
was changed to name the missing subsystem instead, and **step 5 replaced it with a real router**
(§6). `FeatureFlagService.is_enabled` returned `True` for every flag, turning an absent flag service
into a blanket "yes" that could switch on unfinished paths; it was changed to **fail closed**, and
**step 5 replaced that stub with the full service** — which also retired `is_enabled`, since the
class's actual callers want `is_model_allowed` and `effective_tier_for_request`. See §6 for why
fail-closed was the wrong shape for an entitlement check and what it was hiding.
`LlmService.generate` returned `""` and
`generate_course_outline` returned `{}`, the latter surfacing as
`ValueError("Outline contained no modules")` — blaming the model for a method never written.
Both are now implemented on `llm_resilient`, which already does per-user provider selection
across Gemini, OpenAI and Anthropic with circuit breaking and cross-provider fallback. Course
generation works again.

---

## 3. Space feature gates, and a correction

The stub returned `SpaceGateState.ALLOWED`, which reads like an authorization bypass. It was
not, in practice: `SpaceGateState` was a `StrEnum` while callers construct it with keyword
fields, so it raised `TypeError`, and the enum had no `CHAT_GROUP_CREATE` member. Chat-group
creation and group-session start both crashed.

An earlier pass rewrote this and recorded the free-tier limits as needing a product decision,
having guessed them. **That was wrong twice over.** The limits *are* specified — in
`tests/test_circle_gates.py`, an uncollected test file that is the authoritative spec for
every feature, limit, error code and status code. It has been repointed and renamed to
`tests/test_space_gates.py`, and the implementation now satisfies all 26 of its assertions.

What the recovered spec corrected:

- The free group-session allowance is **3**, not the 1 that was guessed.
- A space *with* a plan still has a chat-group ceiling of **10**, reported as
  `409 CHAT_GROUP_LIMIT_REACHED` — a different condition from hitting the free limit
  (`402 CHAT_GROUPS_REQUIRE_CIRCLE_PLAN`). One means "pay to continue", the other "you cannot
  have more".
- There are **11 gated features**, not 2, plus a separate pinned-resource limit of 5.
- `gate()` returns `True` on allow; error codes are per-feature, not one generic code.
- **An unknown feature is allowed, not denied.** The earlier pass made it fail closed and
  described that as a virtue. The recovered behaviour is deliberate: plan gating is not
  authorization, and a newly added feature should not be silently unavailable because nobody
  wrote a rule for it. The cost is that a feature intended to be paid is free until it appears
  in the table, so adding a `SpaceFeature` member and adding its rule must happen together.
  This is called out in the module docstring.

---

## 4. Three broken Celery tasks — now fixed

`src/tasks/` contained nothing but `__pycache__`, and two workers imported from it *inside*
the task body, so the module imported cleanly and the task died when beat fired it.

The originals could not be lifted as they stood: they were Prisma-based and depended on a
parallel `src/tasks` framework (`base`, `registry`, `schedules`) that no longer exists.
Rebuilding that alongside the current Celery layout would have added a second way to declare
tasks, so the logic now lives as plain services in the domain that owns the data, called by
the existing workers. The empty `src/tasks/` package was removed.

- **`notifications.schedule_reminders`** → `progress/services/schedule_reminders.py`.
  Eligibility is now `tier != "FREE"` rather than the original's allowlist of
  `PREMIUM_MONTHLY`, `STUDY_CIRCLE_*` and `SQUAD_*`. Every one of those names is retired, so
  that check would now silently exclude paying subscribers on `plus_monthly` and
  `plus_yearly`. The LLM-drafted subject line was dropped: `ai_email_service` was never
  migrated, and a reminder that must arrive in a 15-minute window should not depend on a model
  call for copy the learner cannot perceive.
- **The due-review sweep** → `process_due_reviews` in `spaced_repetition_impl.py`, beside the
  existing per-item block creation it reuses. Note the scope: the *interactive* side of spaced
  repetition was migrated and works, so reviews came due and were answerable; only the
  scheduled sweep that puts them on the calendar was missing.
- **`notifications.weekly_summary`** → `progress/services/weekly_summary.py`. This previously
  raised by design so the beat task failed visibly rather than faking success. It compares this
  week against the previous one, because a bare number is not actionable, and skips users with
  nothing to report rather than emailing an empty summary.

---

## 5. Paystack and referral rewards are still non-functional

These two modules import, but they cannot work: they still speak the **Prisma** client API
(`db_client.user.find_unique(where=...)`, 31 call sites) and read Prisma's camelCase
attributes (`user.referralCode`, `user.paystackSubscriptionCode`) off SQLAlchemy models.
`prisma` is not in `pyproject.toml`.

Rather than let those paths die on `NameError: name 'db' is not defined`, `db` is bound to a
`PrismaClientRemoved` sentinel that raises on first attribute access, naming the module, the
missing migration, and the fact that no data was read or written.

Stripe and Google Play are clean of Prisma and work. `/plans/catalog` was additionally broken
end to end — the route declared `PlanCatalogResponse(plans=[PlanItem])` while `stripe_service`
built `products=[PlanCatalogEntry]` from the never-created `..schemas.subscription`. It is
rewritten, verified to return the five plans, and `PlanItem` gained a `scope` field so the
personal / circle / add-on grouping the old catalog carried is not lost.

**Cost to migrate**: 31 call sites plus attribute renames across two files.
`referral_rewards_service` is 21 of them and self-contained; `paystack_service` is 10. The
mapping is mechanical (`find_unique` → `select().where()`, `update` → `update()`) and the
`User` model already maps every column, so the renames are one to one.

**This was deliberately not attempted in this pass.** Rewriting payment handling without
integration tests against a Paystack sandbox risks a subtly wrong subscription state, and the
current behaviour — failing loudly with an explanatory error — is safer than a half-verified
migration. `referral_rewards_service` is the lower-risk half and could go first; a wrong token
grant is recoverable in a way that a mishandled subscription is not.

**Decision needed**: Paystack matters for African markets. Is it in scope now, or is Stripe
sufficient for the current stage?

---

## 6. The largest remaining item: the LLM subsystem

The pre-migration `src/services/llm/` package held **23 modules, roughly 240,000 characters**.
The current `llm/` package has six files, three of which were stubs.

Recoverable from `git show "4953972^:apps/backend/src/services/llm/<file>"`:

| Module | Size | What it does |
|---|---|---|
| `router.py` | 17.7k | `route_request`, provider selection, fallback |
| `anthropic_chat_tools.py` | 29.9k | Anthropic tool calling |
| `gemini_chat_tools.py` | 27.3k | Gemini tool calling |
| `openai_chat_tools.py` | 23.3k | OpenAI tool calling |
| `feature_flags.py` | 24.3k | Stored flag definitions, per-scope overrides |
| `tool_normalizer.py` | 14.2k | Cross-provider tool schema normalisation |
| `stream_normalizer.py` | 11.7k | Cross-provider streaming normalisation |
| `circuit_breaker.py` | 9.2k | Per-provider circuit breaking |
| `cost_tracker.py` | 6.8k | Per-request cost accounting |
| `prompts.py`, `context.py`, `capabilities.py`, `metrics.py`, others | ~25k | Supporting layers |

This is what `get_llm_router()` needs, and it is why chat could not produce a response. It also
explains nine of the still-skipped test files, which are tests for these exact modules and are
sitting written and unused.

**Superseded — the migration is complete, steps 1–6, 2026-08-26/27.** `get_llm_router()` returns a
real router, all four provider adapters are ported and registered, and eight of those nine test files
are collected. Step 7 turned out not to be a migration task at all; see the closing record below. The
table above is kept as the inventory of what the pre-migration package held. **The per-step records
below are the current state, and each is dated — read the latest, not the first.** What is left is not
migration work: one real provider call has still never been made, and two configuration gaps stop
`EMBEDDING` and `openai:gpt-4o` from routing.

**Suggested sequence**, each step independently verifiable and each unskipping its own tests:

1. ~~`types.py`, `protocol.py`, `capabilities.py`, `errors.py` — no dependencies.~~ **Done, 2026-08-26.**
2. ~~`circuit_breaker.py` + `cost_tracker.py` — pure logic; unskips 2 test files.~~ **Done.**
3. ~~`tool_normalizer.py` + `stream_normalizer.py` — pure transforms; unskips 2 more.~~ **Done.**
4. ~~One provider adapter end to end (Gemini, since `gemini_sdk.py` already exists).~~ **Done.** Constructed and exercised by step 5.
5. ~~`router.py` — needs 1–4; unskips `test_end_to_end_routing`.~~ **Done, 2026-08-26.** `get_llm_router()` returns a real router; see the step 5 record below.
6. ~~The remaining two adapters (`openai_chat_tools`, `anthropic_chat_tools`) plus `gemini_embedding`; unskips `test_openai_chat_tools`.~~ **Done, 2026-08-27.** See the step 6 record below.
7. ~~`feature_flags.py` with real storage, replacing the fail-closed stub.~~ **The service was restored in step 5. The "real storage" half is not a migration task at all** — it describes something that never existed in any commit. See "step 7 was never a migration task" below. **The migration is complete at step 6.**

### Migration progress, 2026-08-26 — steps 1–4

Ported from `4953972^` into `src/domains/intelligence/reasoning/llm/`. Suite went from 3,057 to 3,202
passing; `ruff` clean. Commits `830889e`, `d5db890`, `83d3476`.

**Ported unchanged apart from import paths** — `types`, `protocol`, `capabilities`, `base_adapter`,
`circuit_breaker`, `tool_normalizer`, `stream_normalizer`, `prompts`, `streaming`, `context`,
`gemini_chat_tools`. The last of these exposes `GeminiChatToolsAdapter.get_chat_response_with_tools`,
which satisfies `protocol.ChatWithToolsProvider` — the 4-tuple `websocket_handler` destructures.

**Ported with a rewrite**, both because the original spoke to something that no longer exists:

- `cost_tracker` — imported `from prisma import Prisma` and used `db.llmcostrecord.create` plus
  `db.query_raw` with hand-numbered `$1` placeholders. Rewritten onto the existing `LlmCostRecord`
  model. The constructor takes `session_factory=None` instead of a client and resolves it *at call
  time*, so a tracker can be built before the database is connected — which the router does.
  `aggregate` now builds a typed `select()`; the original counted placeholders by hand, and a filter
  added later without incrementing the counter would have bound the wrong value to the wrong column.
- `errors` — two incompatible versions existed. The pre-migration one carried `provider`, `model`,
  `status_code`, `category`, `message`, `retriable`; the migration-era one had reduced it to
  `(provider, message, status_code)`. **`websocket_handler` reads `e.category`, `e.model` and
  `e.message` in its `except LLMProviderError` block**, none of which existed on the reduced class — so
  the handler that turns a provider failure into a readable message would itself have raised
  `AttributeError`, inside an `except`. Merged: the rich signature is canonical, the migration-era
  `LLMError` base and `LLMUnavailableError` are kept, `retriable` now defaults from `category` rather
  than to `False`, and `unsupported_capability` was added to `ERROR_CATEGORIES` because
  `_ERROR_CATEGORY_MESSAGES` maps it and the old frozenset did not.

**A tenth unmigrated module, not in the table above:** `src/services/chat_tool_arg_enrichment.py`
existed nowhere in the current tree and had its own skipped test file. Ported to
`src/domains/intelligence/action/tool_arg_enrichment.py`; only `_enrich_note_tool_args` touched the
database, so it gained two small SQLAlchemy helpers.

**Test files un-skipped — 6 of 10.** `pytest_ignore_collect` drops any test file whose source contains
`src.services.`, so retargeting the imports is what un-skips them.

| File | Tests | Changed beyond import paths? |
|---|---|---|
| `test_circuit_breaker.py` | 28 | No |
| `test_tool_normalizer.py` + `test_stream_normalizer.py` | 66 | No |
| `test_llm_chat_context.py` | 3 | No |
| `test_chat_tool_arg_enrichment.py` | 5 | No |
| `test_cost_tracker.py` | 25 | **Yes** — `record`/`aggregate` asserted the Prisma call shape and were rewritten against a fake session; `compute_cost` and `PROVIDER_PRICING` are untouched |

Still skipped: `test_end_to_end_routing.py` (605 lines, needs step 5), `test_feature_flags.py` (775,
step 7), `test_openai_chat_tools.py` (228, step 6), `test_llm_agentic_roundtrip.py` (47 — wants the
legacy `GeminiService` class from `src/services/llm_service.py`, which is *not* on this path; the
adapter is its replacement. Decide whether to port `GeminiService` or delete that test).

**Note on scope**: `llm_resilient.py` (already working, multi-provider) lives under
`domains/personal_learning/services`, the wrong home for a shared client. It is deliberately
left there: existing tests patch
`src.domains.personal_learning.services.llm_resilient.generate_content_json`, and moving it
would silently disable those patches. Relocating it is **still outstanding** — step 5 did not do it,
because nothing in the routing layer touches it and the move is a rename with a test-patch hazard,
not part of restoring the router.

### Migration progress, 2026-08-26 — step 5, the router

`adapter_registry.get_llm_router()` returns a real `LLMRouter`. Suite went from 3,202 to **3,286
passing / 185 skipped**, `ruff check src/ tests/` clean. Four modules added under
`src/domains/intelligence/reasoning/llm/`: `metrics.py`, `feature_flags.py`, `router.py`, and a
rewritten `adapter_registry.py`.

**The import survey the last pause deferred.** `router.py` needs `base_adapter`, `capabilities`,
`circuit_breaker`, `errors` and `registry` — all present from steps 1–4 — plus `metrics` (ported
here) and `feature_flags` (see below). `CircuitState` and `LLM_CIRCUIT_BREAKER_TRIPS` appear on its
import lines and nowhere in its body, so they were dropped. Two lazy imports inside the success path
needed retargeting: `cost_tracker.PROVIDER_PRICING`, and `usage_tracking_service.emit_ai_usage`,
which is now `domains/billing/services/usage_tracking`.

**Ported unchanged apart from import paths** — `metrics`, and `router`'s selection and fallback
logic. `test_end_to_end_routing.py` (11 tests) and `test_feature_flags.py` (65) both pass with no
change beyond their import lines, which is the same fidelity evidence steps 1–3 relied on.

**The sequence was wrong about step 7, and it changed this step's shape.** Step 7 read
"`feature_flags.py` with real storage", implying a storage layer had to be built first. It does not:
the pre-migration module has **no imports beyond `logging` and `typing`**. Its persistence is an
*injected* `FeatureFlagStore` Protocol with a documented `store=None` env-only mode, so it was pure
logic, portable exactly like `circuit_breaker`. It also could not wait, because
`router._select_candidates` calls `is_model_allowed` on it. So it was ported in full here and step 7
shrinks to the dynamic-override path.

**The fail-closed stub was hiding the real failure, not causing a clean one.** The stub exposed only
`is_enabled` and `get_variant`, and neither is what this class is for. `websocket_handler` calls
`feature_flags.effective_tier_for_request(...)` at line 1553 — **before** it reaches
`get_llm_router()` at 1567 — so every substantive turn died on `AttributeError: 'FeatureFlagService'
object has no attribute 'effective_tier_for_request'`, not on the deliberate `raise_unmigrated` that
`get_llm_router` was written to produce. The documented failure mode was not the observed one.

Fail-closed was also the wrong shape for this particular check, and the reasoning is worth keeping:
an unknown *feature flag* reading as off keeps behaviour at the previous default, which is safe. An
*entitlement allowlist* reading as "no model is permitted for anyone" is not a safe default, it is an
inert subsystem, and it is indistinguishable from a misconfigured allowlist. The real service already
denies by default at step 3 of its precedence chain — a pair absent from the tier allowlist is
denied — so configuration decides access rather than a stub.

**Three traps a verbatim port would have hit.** Each was found by checking what the *callers* pass
and what the *collaborators* expose, rather than only the import list — which is the generalised
lesson from Phase 2's four missing `manager` methods:

- `websocket_handler` already calls `route_request(..., space_id=...)` (lines 471, 1586) because the
  Circle→Space rename landed on the caller first. The legacy parameter is `circle_id`. A verbatim
  port raises `TypeError: unexpected keyword argument 'space_id'` on the first real turn — *after*
  the learner's message has been saved, which is the same shape as the Phase 2 failure.
- The legacy router calls `emit_ai_usage(circle_id=...)`. The migrated `emit_ai_usage` takes
  `space_id` and absorbs the rest into `**_kwargs`, and the call site is wrapped in
  `except Exception`. So `circle_id` would have been accepted, dropped, and never logged: every
  space-scoped request would have recorded as unattributed usage with nothing surfacing. Verified
  fixed by asserting the awaited kwargs — `space_id='sp_42'` arrives as a named parameter and
  `circle_id` is absent.
- `feature_flags`' two DB reads spoke to Prisma. `_fetch_personal_tier` used
  `prisma.user.find_unique`; it is now a SQLAlchemy `select` on `User.tier`. `_fetch_seat_tier`
  imported `src.services.seat_service.get_seat_tier` while a second module-level helper
  (`read_seat_tier_for_user`) read `CircleMember.seatTier` through Prisma directly — two paths to one
  fact. Both now delegate to the migrated
  `learning_spaces.services.seat_impl.get_seat_tier`, which reads `SpaceMember.seat_tier` and already
  honours the "FREE_SEAT on absence or failure" contract the legacy docstring promised.

**`system_config_service` is gone, and LLM config is no longer runtime-tunable.** The legacy registry
read every value through that module's private `_cache` and `_CACHE_TTL` to get DB-stored config
synchronously, with `Settings` as the fallback. The module was not migrated, so the registry now
reads `Settings` directly — which was already the fallback path. **The consequence: LLM
configuration cannot be changed from the admin dashboard, and `invalidate_llm_router()` only picks up
in-memory changes.** Nothing regresses today because the admin router is also commented out, but this
is a capability that existed before the migration and does not now.

**Three adapter blocks were removed rather than carried.** The legacy registry registered OpenAI,
Anthropic and Gemini-embedding adapters through function-local imports inside
`try/except Exception`. Those modules are step 6 and do not exist, and `tests/test_local_imports.py`
failed on all three — correctly, since that guard exists precisely because a function-local import of
an absent module is invisible until the line runs. It has no allow-list, deliberately, so the blocks
were deleted with a comment naming what step 6 restores. Behaviour is identical either way: nothing
was ever registered under those keys, and the router skips a `provider:model` with no adapter. So
`openai` appearing in `LLM_ENABLED_PROVIDERS` and in both fallback chains is inert, not broken.

**What was verified, and what was not.** `get_llm_router()` builds a router with the two Gemini
adapters; `_select_candidates` returns both pairs for `chat_default`, `chat_tools_session` and
`structured_completion` on free and plus tiers, and `[]` for `embedding` (step 6). A full
`route_request` through the registry-built router completes with a fake adapter: candidate selection,
circuit-breaker success, metrics, cost record, usage emission. The handler's exact kwargs bind to
`route_request` in both personal and space-scoped form. **Not verified: any real provider call, and
anything requiring SQL.** There is no local database — `DATABASE_URL` points at remote managed
Postgres and Docker is not running — so 185 tests still skip. The `emit_ai_usage` and
`CostTracker.record` writes have been exercised only against mocks.

**Migration `049` was applied 2026-08-27** against the remote database, after this record was first
written; see §8. The three columns exist with the intended nullability, and all 972 existing
`ChatMessage` rows read `citations IS NULL`, `askMode IS NULL`, `truncated = false` — no backfill, as
the migration specifies. That does not change the paragraph above: the tests that need SQL still skip,
because they need a database they can *write* to, and this one holds real data.

**Still skipped, and why:**
`test_llm_agentic_roundtrip.py` (47 — still wants the legacy `GeminiService`; the open decision to
port it or delete the test is unchanged); `test_chat.py`, `test_circle_billing.py`,
`test_circle_repository.py`, `test_moderation_service.py` (pre-domain architecture).

Two more are now *nearly* recoverable and were left alone because both need more than a retarget —
worth knowing since both cover code this step touched:

- `test_usage_tracking_scope.py` (261 lines) imports `get_circle_usage_summary` and
  `get_personal_usage_summary`, neither of which exists in the migrated `usage_tracking`. Porting
  those two readers would un-skip it, and it is the test that would have caught the `circle_id`
  attribution trap above.
- `test_seat_service.py` (381 lines) imports `SeatServiceError`, `get_seat_tier`,
  `reconcile_seat_pool_on_addon_change` and four error constants from `seat_service`, which currently
  re-exports only four mutation wrappers; the rest live in `seat_impl` or not at all.

### Migration progress, 2026-08-27 — step 6, the remaining adapters

`openai_chat_tools.py` (629), `anthropic_chat_tools.py` (746) and `gemini_embedding.py` (135) ported,
and the three registry blocks step 5 removed are restored. Suite **3,286 → 3,325 passing / 185
skipped**, `ruff` clean.

**All three are byte-identical to `4953972^` apart from import lines.** Verified by diffing each
against the recovered original and filtering import rewrites: the only other change is `ruff`'s isort
reflowing one two-symbol import in `openai_chat_tools` onto separate lines. `test_openai_chat_tools.py`
(23 tests) passes with nothing but its imports retargeted. This step needed no rewrite at all, which
is the difference between it and steps 2 and 5 — nothing these modules touch had moved underneath
them.

The retarget map was already established by the Gemini adapter ported in step 4, and reused verbatim:

| From | To |
|---|---|
| `src.services.llm.<x>` | `src.domains.intelligence.reasoning.llm.<x>` |
| `src.services.llm_registry` | `src.domains.intelligence.reasoning.llm.registry` |
| `src.services.chat_tool_arg_enrichment` | `src.domains.intelligence.action.tool_arg_enrichment` |
| `src.services.skills[.handlers]` | `src.domains.intelligence.action.skills[.handlers]` |
| `src.services.storage_service` | `src.shared.infrastructure.storage_service` |

**No traps this time, and one non-trap worth writing down** so nobody else stops to check it. Neither
chat adapter defines a `provider_name` *property*, which looks like an unimplemented abstract member
on `BaseProviderAdapter`. They set `provider_name = "openai"` / `"anthropic"` as class attributes,
which satisfies `ABCMeta` — it checks for the name, not for a property. Gemini uses a property and the
other two use attributes; all three instantiate. Confirmed via `__abstractmethods__` being empty on
each.

**+39 tests, accounted for exactly:** 23 from `test_openai_chat_tools`, 13 new parametrized cases in
`test_local_imports` (349 → 362) from the adapters' function-local imports, and 3 in
`test_module_imports` (307 → 310), one per new module.

**Cross-provider fallback now actually engages, and that is new.** Before this step the registry held
only two Gemini pairs, so every "fallback" was Gemini→Gemini. Driving the registry-built router with
both Gemini pairs failing retriably now yields the attempt order
`gemini:gemini-3.5-flash → gemini:gemini-3.1-flash-lite → openai:gpt-4o-mini`, answered by OpenAI, at
`MAX_ATTEMPTS`. Five adapters are registered: two Gemini chat, Gemini embedding, and two OpenAI.

**Two configuration gaps that porting adapters does not fix.** Both are faithful to the pre-migration
config defaults, so neither is a port defect — and neither is visible from the adapter code, which is
why they are recorded here rather than left to be rediscovered:

- **`LlmTask.EMBEDDING` still routes to nothing.** `gemini:gemini-embedding-001` is registered and
  reports `EmbeddingCapability`, and it is the sole entry in the EMBEDDING fallback chain — but
  `_select_candidates` returns `[]` on both tiers, because filter 2 (`is_model_allowed`) requires the
  pair to appear in a tier allowlist and neither `LLM_TIER_ALLOWLIST_FREE` nor `..._PLUS` lists it.
  So embedding routing has never worked through the router, before or after the migration. Deciding
  it is worth a thought rather than a config line: an embedding is a system-internal call, and gating
  it behind a subscription allowlist is arguably the wrong shape, whereas adding it to both tiers
  makes a per-tier gate that is always open. Left as-is pending that call.
- **`openai:gpt-4o` is registered but unreachable.** It is in `FALLBACK_CHAT_TOOLS` and in the
  registry, but no tier allowlist contains it — `LLM_TIER_ALLOWLIST_PLUS` has `openai:gpt-4o-mini`,
  not `openai:gpt-4o`. So `chat_tools_session` on plus stays Gemini-only. Whether that is intentional
  cost control or an oversight in the defaults is a product question, not a migration one.

**Anthropic is ported but inert**, and correctly so: it is absent from `LLM_ENABLED_PROVIDERS` and has
no API key configured, so nothing registers under `anthropic:*` despite it appearing in both fallback
chains. It has no test file — there never was one — so unlike OpenAI its port is verified only against
the protocol, the ABC and the registry, not against behaviour.

**The `try/except` around each adapter block now means something different.** In step 5 those blocks
were guarding a missing *module*, which is what `tests/test_local_imports.py` forbids and why they
were deleted. All four modules now exist, so the guard is against a constructor raising — a malformed
key, or a provider SDK changing its signature — and its purpose is that one provider failing to
register does not take the other two down.

**Still not verified: any real provider call.** Every adapter is exercised against mocks or fakes. The
OpenAI adapter has 23 real unit tests behind it; Anthropic and the embedding adapter have none. No
request has gone to OpenAI or Anthropic.

### Step 7 was never a migration task — the migration is complete at step 6

Surveyed 2026-08-27, before writing any code, and no code was written as a result.

Step 7 read "`feature_flags.py` with real storage, replacing the fail-closed stub". The stub was
replaced in step 5. The "real storage" half **describes a capability that has never existed in any
commit of this repository**, so there is nothing to migrate and the sequence ends at step 6.

Four checks, all against `4953972^` and the current tree:

| Check | Result |
|---|---|
| Concrete `FeatureFlagStore` implementation anywhere | **None.** The only file mentioning `get_all_flags` / `get_user_override` is `feature_flags.py`, i.e. the Protocol itself |
| Callers constructing `FeatureFlagService` | **One**, `adapter_registry.py:228`, passing `enabled_providers` and `tier_allowlists` and **no `store=`** |
| Consequence for `reload()` | `self._store` was always `None`, so it always returned at its first branch. `_user_overrides` was only ever written by the in-memory `set_user_override` |
| `FeatureFlag` table or model, SQLAlchemy or migration | **None** |

So the entire grant/revoke/override surface — `set_user_override`, `grant_user_access`,
`revoke_user_access`, `remove_user_override`, `is_user_revoked`, `has_user_override`,
`get_available_models_for_user` — was unreached before the migration and is unreached now: **zero
callers outside `feature_flags.py`.** The one apparent hit is a docstring line in
`adapter_registry.py`.

**It was kept anyway, and that is deliberate.** `test_feature_flags.py` covers this surface with 65
passing tests, and it is the natural seam for the feature if it is ever built. Deleting tested code to
raise a coverage-of-reachable-code number would make the eventual feature a rewrite instead of a
wiring job. It is dead, it is honest about being dead, and it costs nothing.

**What the two halves would actually take**, if product wants them. Both are **net-new feature work
with no consumer today** — the admin router is commented out in `app.py` — so neither was built:

- **Persistent per-user provider overrides.** Needs a table (there is no `FeatureFlag` model), a
  migration, a SQLAlchemy `FeatureFlagStore` implementing the two Protocol methods, and `store=` wired
  at the single construction site. The service side is already written and tested against it.
- **Admin-tunable LLM config.** This is the capability step 5 recorded losing. The pre-migration
  `system_config_service.py` (159 lines) was a `SystemConfig` key-value table with a 60-second
  in-memory cache, read by the registry to override `LLM_ENABLED_PROVIDERS`, the tier allowlists and
  both fallback chains without a redeploy. **There is no `SystemConfig` model or migration in the
  current tree**, so restoring it means modelling the table as well as porting the service — and it
  only becomes reachable once the admin router is mounted. Until then, `Settings` is the single source
  and `invalidate_llm_router()` only picks up in-memory changes.

**Where the LLM subsystem now stands.** Steps 1–6 done. Five adapters register; the router selects,
falls back across providers, records cost, and emits scoped usage; eight of the nine written-and-unused
test files now run. Two things still gate a real answer, and neither is in §6: the two configuration
gaps above (`EMBEDDING` and `openai:gpt-4o` route to nothing), and the fact that **no request has ever
been made to a real provider from this codebase.**

---

## 7. Remaining stubs

22 stub functions across 11 modules, down from 40 across 22. None are the dangerous kind any
more — everything that corrupted data, bypassed a check or discarded a user action is fixed.
What remains is almost entirely the chat subsystem. These were blocked behind the missing router,
which step 5 restored (§6), so they are now individually worth doing — and a turn that reaches the
model will start exercising them, so their stub behaviour is now visible rather than unreachable:

- `conversation/chat_greeting.py` (4), `chat_helpers.py` (7), `component_response.py` (3),
  `session_service.py` (2) — chat message assembly
- `identity/onboarding.py` (5) — conversational onboarding
- `action/action_service.py` (2) — tool dispatch
- `intelligence/memory/memory_service.py` (1) — memory retrieval
- `knowledge_base_service.py` (2), `kb_context_service.py` (2) — retrieval, blocked on the
  vector-store decision in §8
- `personal_learning/services/document_generation.py` (2) — document generation
- `billing/referral_service.py` (1) — returns `0` because the real implementation is one of
  the Prisma-era functions in §5

---

## 8. Database

Audited read-only against the live database. The direct `db.<ref>.supabase.co` host is
IPv6-only and unreachable from here; use the session-mode pooler (port 5432) for DDL, and
transaction mode (6543) for reads. Session mode caps at 15 clients, which is easy to exhaust
with repeated runs.

| Check | Result |
|---|---|
| Orphaned `QuizSession.topicId` | 0 |
| `QuizSession` rows | 6 total, 0 with `topicId` set |
| `ExamPrep.status` | `COMPLETED` 12, `SETUP` 8 |
| `PrepTopic.status` | `NOT_STARTED` 75, `MASTERED` 1 |
| `QuizSession.status` | `IN_PROGRESS` 5, `COMPLETED` 1 |
| `AuditLog` | exists, 7 columns, 0 rows |
| `Embedding` | exists, 15 rows |

No unexpected status values, so no enum widening is needed.

**`topicId` was investigated and is fine.** All six rows being NULL is legitimate: a quiz
spanning every topic is stored with no single topic. `topicId` is mapped and persisted
correctly through `_map_quiz_session`. Because that mapping is a dict lookup that silently
drops unknown keys, a rename or typo would discard the value with no error, so two tests now
pin it — per-topic mastery and readiness both depend on it.

### Migration 013 applied and verified

`013_add_quiz_session_topic_fk` is applied. Verified in the database:
`QuizSession_topicId_fkey FOREIGN KEY ("topicId") REFERENCES "PrepTopic"(id) ON DELETE SET NULL`
plus `QuizSession_topicId_idx`. `SET NULL` rather than `CASCADE` so deleting a topic detaches
the attribution without destroying the practice history the learner earned.

### ~~Migration 014 written, NOT applied — needs your approval~~ Already applied; this section was wrong

**Corrected 2026-08-27.** This said `014` was awaiting approval. It is not: the database reports
`alembic current` well past it, and `select to_regclass('public."Embedding"')` returns `NULL` — the
table is gone and the 15 rows went with it. Nothing is pending approval and there is nothing left to
decide here. It was found while checking, before applying `049`, that no destructive migration was
sitting in the pending set; the answer is that `014` had already run and this section had gone stale.

**What that means in practice.** The 15 rows are unrecoverable unless a dump predates the upgrade.
They were unreachable by design, which is why the migration existed — `vector` is `jsonb`, which
Postgres cannot index or search by nearest neighbour, nothing wrote it, Pinecone is gone, and
`rag_service` reports `available = False`. So the loss is almost certainly immaterial, but it is a
loss that happened without the sign-off this document was holding out for, and saying so is the point.

**Still worth doing:** delete the `Embedding` model from `knowledge/db_models.py`. The table is gone
and the model still maps it, so any query through it fails at runtime rather than at import.

When retrieval returns it should use `pgvector` in this same database, so an embedding and the
row it describes commit in one transaction. That is a new table with a real vector column, not
a revival of this one.

---

## 9. Lint and CI

`ruff` findings in `src` + `tests`: **556 → 0.**

567 were mechanical autofixes (`Optional[X]` → `X | None`, import ordering,
`datetime.timezone.utc` → `datetime.UTC`). `openapi.json` was byte-identical afterwards,
confirming those rewrites were purely syntactic. The last 17 were resolved as explicit,
commented `per-file-ignores` rather than by contorting working code, because each is a false
positive:

- `N806` on UPPER_CASE locals in three files that are genuine constants built from heavy
  optional dependencies (`python-pptx`, `weasyprint`) imported lazily inside the function on
  purpose. Hoisting them to module level would defeat the lazy import.
- `N811` on the aliased lazy `weasyprint` import.
- `N804` on `_LazyModule.__getattr__`, which subclasses `ModuleType`, so `self` is correct;
  ruff reads the `type(...)` base as a metaclass.
- `E402` in `alembic/env.py`, which must configure before importing models.

**CI was quietly failing.** It ran `flake8`, which reported
`trial_service.py:310 F811 redefinition of unused 'timezone'` — a redundant function-local
`from datetime import datetime, timezone` shadowing the module-level import, left unused by the
`UP017` rewrite. Fixed.

CI now runs `ruff check src tests alembic` in place of `flake8`, and flake8 has been retired:
its config, its dependency and its lock entry are gone. Ruff's `E`/`F`/`W` rules cover what
flake8 checked, and keeping two linters meant two ignore lists to drift apart — the old
`.flake8` still carried a `per-file-ignores` entry for `src/services/llm_service.py`, a path
that has not existed for some time. Also fixed: `[project.license]` was a table, which made
every `poetry` invocation print a deprecation warning; `poetry check` is now clean.

### Black and isort are retired too — ruff formats as well as lints

The paragraph above says flake8's config was gone. **`.flake8` was still on disk**, and its
comment still read *"This project uses flake8 and black for linting/formatting"* — a file
describing a setup that had not existed for months. It is deleted now, along with black and
isort.

The reason is that layout had two owners. `[tool.black]` and `[tool.ruff]` each declared
`line-length = 100` and a py312 target, so a change to one was a silent divergence from the
other, and the lint config carried `E203` in its ignore list with a comment about keeping Black
as the source of truth. `E203` is **preview-only in ruff**, so that ignore had never had any
effect — it was guarding against a check that was never running. isort was worse: the dependency
and `[tool.isort]` were present, and nothing in CI or the Dockerfile had ever invoked it. Ruff's
`I` rules have been sorting imports the whole time.

`ruff format` disagreed with black on **3 of 548 files**, and in each case black was the worse
output: it wrapped a 108-character string in parentheses that left the line over the limit, split
an `assert` into a parenthesised condition plus a message that was still too long, and collapsed a
four-deep nested ternary onto one line. The formatter now runs in CI as
`ruff format --check --diff .`, which names the offending lines rather than only the files.

**What this did not fix, and should.** The ruff ignore list was copied wholesale from `.flake8`
when the project migrated, which carried flake8's compromises across: `F401` hides **165 unused
imports** and `F841` hides **15 unused local variables**, none of them in `__init__.py`, so none
is a legitimate re-export. An assigned-but-unused variable is usually the trace of a code path
that was deleted, which is the same class of defect as a writer nobody calls. `ruff check --fix`
clears the imports mechanically; the variables want reading one at a time, so they are left for
their own pass rather than folded into a tooling change.

---

## 10. Tests

**1087 passing, 10 skipped**, from 465 at the start.

Added this pass: `test_module_imports.py` (242), `test_local_imports.py` (223 + a
non-vacuity guard), `test_email_infrastructure.py` (33), `test_push_notifications.py` (15),
`test_restored_stubs.py` (46), `test_background_tasks.py` (23), plus the recovered
`test_space_gates.py` (26) and two `topicId` mapping guards.

**Skipped at collection: 24 → 20 → 12 → 11** (as of step 6, 2026-08-27).
`conftest.pytest_ignore_collect` skips any file containing `src.services.`, `src.routes.`,
`src.core.database` or `src.schemas.subscription`. Recovered so far: `test_cost_calculator`,
`test_credit_service`, `test_gemini_tool_handlers` (11) and `test_space_gates` (26) in the earlier
pass; then `test_circuit_breaker` (28), `test_tool_normalizer` + `test_stream_normalizer` (66),
`test_llm_chat_context` (3), `test_chat_tool_arg_enrichment` (5) and `test_cost_tracker` (25) in
steps 1–4; then `test_end_to_end_routing` (11) and `test_feature_flags` (65) in step 5; then
`test_openai_chat_tools` (23) in step 6.

The remaining 11 are **not dead and should not be deleted**:

- **1 is a test for an LLM module that was deliberately not restored** (§6):
  `test_llm_agentic_roundtrip` wants the legacy `GeminiService`, which is not on the router path — the
  adapter replaced it. Open decision to port or delete, unchanged since step 4.
- **4 are Prisma-era**, asserting Prisma call shapes rather than behaviour, and need rewriting
  rather than repointing: `test_auth` and `test_password_reset` import a `db` global that no
  longer exists; `test_usage_tracking_scope` asserts
  `db.aiusagerecord.find_many(where={"circleId": ...})` — both a Prisma API and a pre-rename
  column. `test_usage_tracking_scope` also imports `get_circle_usage_summary` and
  `get_personal_usage_summary`, which the migrated `usage_tracking` does not have. It is worth
  raising in priority: it is the test that would have caught step 5's `circle_id` usage-attribution
  trap (§6).
- **`test_seat_service`** needs 13 symbols from a module that has 4. The missing nine include
  `SeatServiceError`, five error-code constants, `get_seat_tier`,
  `reconcile_seat_pool_on_addon_change` and `release_seat_on_member_remove`. The seat-pool
  reconciliation logic is real work, not a repoint.
- **The rest** target modules that do not exist (`circle_billing_service`,
  `moderation_service`, `circle_repository_service`) or are themselves stubs (`chat_helpers`).

Two things worth knowing about the harness. CI sets `DATABASE_URL`, so the 10 tests skipped
locally for want of a database **do run in CI**. And `conftest`'s autouse `db_lifecycle`
fixture skips any test when `DATABASE_URL` is unset, so a pure unit-test file must set
`os.environ.setdefault("SKIP_DB_FIXTURE", "1")` before its imports or it will silently skip
in full.

---

## 11. An authorization trap, now closed

`shared/auth/dependencies.require_space_membership` carried a `TODO` and returned the user
unconditionally. A dependency whose name promises enforcement enforced nothing.

It was **not a live bypass**: nothing had adopted it, so no endpoint was exposed. But it was
exported from `shared.auth` beside the working guards, which made it look ready to use, and
`SpaceMember` has existed in SQLAlchemy for some time — the only thing standing between this
and a real hole was that nobody had reached for it yet.

Now implemented, with one deliberate choice: a non-member gets **404, not 403**. Whether a
given space exists is not something a non-member should be able to probe by comparing status
codes. `tests/test_space_membership_guard.py` covers all four outcomes including that one.

A sweep for similar shortcuts (`skip check`, `for now`, `always allow`, `bypass`) turned up 18
other hits, all documented simplifications in business logic rather than auth. None grant
access.

---

## 12. Outside the backend

These were found by looking beyond `apps/backend`, which earlier passes had not done.

### The frontend has no typecheck, lint or test gate

`maigie-client` has three workflows: `api-types-check.yml`, `cloudflare-pages.yml` and
`cloudflare-pages-admin.yml`. The first typechecks **only `libs/types`**. The other two run
`nx build`, and a Vite/esbuild build strips types without checking them.

So a build can go green while `tsc --noEmit` fails, which is exactly the situation:

- **22 `tsc` errors in `apps/web` are unguarded.** They break down as 10 `TS2339`
  (property missing, mostly mock types that have drifted from the components reading them),
  6 `TS2345` (a `string` passed where a literal union is required), 2 `TS2300`
  (duplicate `HomeResponse` identifier in `useOnboarding.ts`), and one each of `TS1501`
  (a regex flag needing an `es2018` target), `TS18047` (possibly-null), `TS7016` (no
  declarations for `three`), and `TS2786` (`Outlet` not usable as a JSX component).
- **14 client test files never run in CI.**
- There is no lint step, and `package.json` has no `lint`, `test` or `typecheck` script.

The fix is the same shape as the backend's: clear the 22 errors, then add the gate. Adding
the gate first would land a knowingly red pipeline, which is why it was not done here.

`maigie-public` has no workflows at all.

### The misplaced-workflow pattern was duplicated

`maigie-client/.github/ISSUE_TEMPLATE/workflows/backend-ci.yml` — a **backend** workflow, in
the **frontend** repo, in a path GitHub never reads. The same dead file was removed from
`maigie` earlier in this pass. Deleted.

### Frontend cleanup already has its own plan

`maigie-client/docs/WEB_FRONTEND_CLEANUP_PLAN.md` is a tiered plan that is partly executed,
so it should be the source of truth rather than duplicated here. Its own "Still outstanding"
table lists six items: `ReviewsPage`/`features/reviews` (API-wired with no replacement, still
in the sidebar), `features/resource-bank`, the now-unreachable Goals CRUD cluster (held for
in-flight work), 11 parked Tier 1.5 files awaiting the credits/earn decision, and an audit of
the partially-used service layer (`coursesApi`, `notesApi`, `examPrepApi` and others).

There are **33 mock files across 15 features**. Per that plan the mocks are the intended
target state for now, not debt to delete, so they are counted here as scope rather than rot.

---

## 13. Still open, in priority order

1. ~~**Approve or reject migration `014`** (§8) — destroys 15 unreachable rows.~~ **Moot: it was
   already applied.** Verified 2026-08-27, the `Embedding` table does not exist (§8). What remains is
   a two-line cleanup: delete the now-unmapped `Embedding` model at `knowledge/db_models.py:521` and
   its mention in that module's docstring.
2. **Decide on Paystack and referral rewards** (§5) — 31 call sites, deliberately not attempted
   without a payment sandbox.
3. ~~**The LLM subsystem** (§6) — the single largest item; unskips 9 test files.~~ **Migration
   complete, steps 1–6, 2026-08-26/27.** Step 7 was never a migration task (§6). Eight of the 9 test
   files run. What replaces this item, in order:
   - **Send one real request to a provider.** Nothing in this codebase has ever done so. Everything
     is verified against mocks, so the first live call is where remaining defects surface.
   - **Decide the two configuration gaps** (§6): `LlmTask.EMBEDDING` and `openai:gpt-4o` are
     registered but route to nothing, because no tier allowlist lists them. Both are product calls,
     not migration work.
   - **Decide on admin-tunable LLM config** (§6) — a capability that existed pre-migration and does
     not now. Needs a `SystemConfig` model, which no longer exists, and a mounted admin router.
4. **Device-token registration** (§2) — until an endpoint writes `DeviceToken` rows, push
   cannot deliver. Tied to mobile scope.
5. **The chat subsystem stubs** (§7) — 22 functions. No longer blocked behind item 3; the router
   exists, so these are now the next thing a real turn hits.
6. **`AuditLog.adminUserId`** (§2) — `NOT NULL` with an `ON DELETE SET NULL` FK; needs a
   migration to reconcile.
7. **The web Prepare surface is still 100% mocks.** Phases 5–6 of the integration plan are not
   started, and `apps/web/src/features/exam-prep/services/examPrepApi.ts` (449 lines) is dead,
   held alive only by type-only imports of `QuizMode` and `QuizQuestion` in two mock files.
   Moving those two types into the mocks unblocks its deletion.
8. **Clear the 22 `apps/web` type errors, then gate the frontend** (§12) — currently no
   typecheck, lint or test runs in client CI, and a passing build proves nothing about types.
9. **The six items in the frontend cleanup plan** (§12) — that document owns them.

## Not addressed, deliberately

- A single benign pydantic warning about an `alias="referralCode"` having no effect. It does not
  reproduce on direct import and both visible usages look correct. Pre-existing, and not
  proportional to chase further.
- Multi-worker websocket fan-out. The connection registry is in memory, so with more than one
  worker a message reaches only the worker holding that user's socket. Needs a shared broker.
- `check_freebusy` / `has_conflict` in the calendar integration, and `log_user_activity` in the
  audit service. All three exist in history and nothing calls them; restoring them would be
  adding dead code. Note that `handlers.py` returns a hardcoded `"has_conflicts": False`, which
  is where a real conflict check would belong if that feature is wanted.
