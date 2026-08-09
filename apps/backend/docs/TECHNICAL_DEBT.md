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
now names the missing subsystem instead. `FeatureFlagService.is_enabled` returned `True` for
every flag, turning an absent flag service into a blanket "yes" that could switch on
unfinished paths; it now **fails closed**. `LlmService.generate` returned `""` and
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

This is what `get_llm_router()` needs, and it is why chat cannot produce a response. It also
explains nine of the still-skipped test files, which are tests for these exact modules and are
sitting written and unused.

**Suggested sequence**, each step independently verifiable and each unskipping its own tests:

1. `types.py`, `protocol.py`, `capabilities.py`, `errors.py` — no dependencies.
2. `circuit_breaker.py` + `cost_tracker.py` — pure logic; unskips 2 test files.
3. `tool_normalizer.py` + `stream_normalizer.py` — pure transforms; unskips 2 more.
4. One provider adapter end to end (Gemini, since `gemini_sdk.py` already exists).
5. `router.py` — needs 1–4; unskips `test_end_to_end_routing`.
6. The remaining two adapters; unskips their test files.
7. `feature_flags.py` with real storage, replacing the fail-closed stub.

**Note on scope**: `llm_resilient.py` (already working, multi-provider) lives under
`domains/personal_learning/services`, the wrong home for a shared client. It is deliberately
left there: existing tests patch
`src.domains.personal_learning.services.llm_resilient.generate_content_json`, and moving it
would silently disable those patches. Relocating it belongs in step 5.

---

## 7. Remaining stubs

22 stub functions across 11 modules, down from 40 across 22. None are the dangerous kind any
more — everything that corrupted data, bypassed a check or discarded a user action is fixed.
What remains is almost entirely the chat subsystem, where restoring any one piece achieves
nothing while the router is missing:

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

### Migration 014 written, NOT applied — needs your approval

`014_drop_embedding_table` is **destructive**: it deletes 15 rows. The table is unreachable by
design — `vector` is `jsonb`, which Postgres cannot index or search by nearest neighbour,
nothing writes it, Pinecone is gone, and `rag_service` reports `available = False`. `downgrade`
restores the structure but **not the data**. If approved, delete the `Embedding` model in
`knowledge/db_models.py` in the same change.

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

---

## 10. Tests

**1087 passing, 10 skipped**, from 465 at the start.

Added this pass: `test_module_imports.py` (242), `test_local_imports.py` (223 + a
non-vacuity guard), `test_email_infrastructure.py` (33), `test_push_notifications.py` (15),
`test_restored_stubs.py` (46), `test_background_tasks.py` (23), plus the recovered
`test_space_gates.py` (26) and two `topicId` mapping guards.

**Skipped at collection: 24 → 20.** `conftest.pytest_ignore_collect` skips any file containing
`src.services.`, `src.routes.`, `src.core.database` or `src.schemas.subscription`. Recovered so
far: `test_cost_calculator`, `test_credit_service`, `test_gemini_tool_handlers` (11 tests) and
`test_space_gates` (26).

The remaining 20 are **not dead and should not be deleted**:

- **9 are tests for the unmigrated LLM subsystem** (§6). They unskip as those modules land.
- **4 are Prisma-era**, asserting Prisma call shapes rather than behaviour, and need rewriting
  rather than repointing: `test_auth` and `test_password_reset` import a `db` global that no
  longer exists; `test_usage_tracking_scope` asserts
  `db.aiusagerecord.find_many(where={"circleId": ...})` — both a Prisma API and a pre-rename
  column.
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

1. **Approve or reject migration `014`** (§8) — destroys 15 unreachable rows.
2. **Decide on Paystack and referral rewards** (§5) — 31 call sites, deliberately not attempted
   without a payment sandbox.
3. **The LLM subsystem** (§6) — the single largest item; unskips 9 test files.
4. **Device-token registration** (§2) — until an endpoint writes `DeviceToken` rows, push
   cannot deliver. Tied to mobile scope.
5. **The chat subsystem stubs** (§7) — 22 functions, mostly blocked behind item 3.
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
