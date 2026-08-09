# Technical debt: what was paid down, and what is left

Written 2026-08-09, after a pass over the backend. Every claim here was checked against
the code, the test suite, or the live database, and the check is named so it can be
repeated. Where something is a judgement call or needs a product decision, it says so.

---

## 1. The finding that reframes the rest

**Eleven modules could not be imported at all.** Not "were incomplete" — `import` raised.

The application still started, because every one of them is reached lazily: either the
import sits inside a request handler, or the module is only pulled in by a code path that
runs on demand. So the failure surfaced the first time a real user touched the feature,
and never in CI.

What was broken:

| Module | Broken import | User-visible effect |
|---|---|---|
| `billing/services/stripe_service` | `..config`, `..schemas.subscription` | Stripe checkout, webhooks, plan catalog |
| `billing/services/paystack_service` | `..config`, `..core.database` | All Paystack payment handling |
| `billing/services/google_play_service` | `..config` | Google Play purchase verification |
| `billing/services/referral_rewards_service` | `..core.database` | Referral signup and reward tracking |
| `billing/services/credit_consumption_service` | `src.utils.exceptions` | Credit checks and consumption |
| `billing/services/credit_purchase_service` | `src.utils.exceptions` | Credit pack purchase fulfilment |
| `knowledge/services/course_delete_service` | `src.utils.exceptions` | Course deletion |
| `personal_learning/services/note_impl` | `src.models.notes` | ~10 notes endpoints |
| `intelligence/conversation/note_service` | (chain via `note_impl`) | Note lookup from chat |
| `intelligence/conversation/websocket_handler` | `src.utils.exceptions`, `src.models` | The entire chat websocket |
| `intelligence/reasoning/llm/*` | (see §4) | Chat responses |

All eleven now import. The mechanical cause was a domain reorganisation that moved files
without updating the paths they import, against packages (`src.utils`, `src.models`,
`src.services`, `src.domains.billing.config`) that were never created. `src/tasks/`
exists but is empty, and two workers still import from it.

**The guard**: `tests/test_module_imports.py` parametrises over
`pkgutil.walk_packages` and asserts all 242 modules import. Importing a module proves only
that its imports resolve, which is exactly the class of breakage that slipped through, and
it costs about five seconds.

**Recommendation**: CI runs `black --check` and `pytest` but **not `ruff`**. That is why
556 lint findings accumulated. Adding `ruff check src tests` to `.github/workflows/backend-test.yml`
prevents the recurrence.

---

## 2. Silent failures that were fixed

Each of these accepted a call, returned a plausible value, and did nothing.

### Email — was `pass`
`shared/infrastructure/email.py` had every function as `pass`. Space invitations and
credit-limit notices were accepted and discarded.

Restored from `git show "4953972^:apps/backend/src/services/email.py"`: SMTP via stdlib
`smtplib`, Resend via HTTP, ordered by `EMAIL_OUTBOUND_STRATEGY` so a provider quota
failure falls through to the next provider instead of dropping the message.

Three deliberate departures from the original:
- `fastapi_mail` dropped. It supplied three booleans; the sending was always `smtplib`.
- Sender address and frontend URL resolve per send, not at import, so settings changed
  afterwards take effect.
- `select_autoescape(html, xml)` rather than blanket autoescaping, or the plaintext parts
  would gain HTML entities.

**A latent bug was fixed in passing**: `send_space_invite_email` was declared
`(to_email, space_name, inviter_name, ...)` but its only caller passes
`(email, inviter_name, space_name)` positionally. Any implementation written against the
stub would have addressed the space by the inviter's name.

Templates `circle_invite.*` were renamed to `space_invite.*` with the space vocabulary.

`send_weekly_summaries` raises `NotImplementedError` rather than returning quietly: it is
driven by a Celery beat task, and the original lived in a separate 13k module with its own
repository dependencies. A weekly job that reports success while sending nothing is worse
than one that fails.

### Push notifications — was `pass`
Restored onto SQLAlchemy. Two changes: dead FCM tokens are **deleted** rather than flagged,
because the `DeviceToken` model has no `isActive` column and an `UNREGISTERED` token is
dead permanently; and `messaging.send_each` now runs via `asyncio.to_thread`, since it
performs blocking HTTP and the original stalled the event loop for a whole fan-out.

**It still cannot deliver, for a reason outside this module**: nothing writes `DeviceToken`
rows. There is no registration endpoint. Every send returns `no_tokens`, which is reported
honestly. Mobile is out of scope, which is why the registration surface does not exist.

### Cost and revenue — was `return 0.0`
`calculate_ai_cost` and `calculate_revenue` returned `0.0` for every call, so cost tracking,
revenue attribution and margin reporting all read zero regardless of usage. That is worse
than absent data, because the numbers look valid.

Ported intact. The restoration is independently confirmed: `tests/test_cost_calculator.py`,
written against the original implementation, was repointed and passes unmodified.

### Admin audit trail — was `pass`, and the arguments were discarded
The stub was `log_admin_action(action, admin_id="", **kwargs)`; the caller passes
`admin_user_id`, `resource_type`, `resource_id` and `details` by keyword, so all four were
absorbed by `**kwargs`. An administrator adjusting a user's credit balance left no record.

The `AuditLog` table **already exists** in the database (7 columns, 0 rows) with no
SQLAlchemy model. Added `domains/admin/db_models.py` mapped to the live shape, including
the five real indexes so an autogenerate diff stays empty. **No migration needed.**
Records now go to the table plus a structured log line, and neither failure can propagate:
an audit write must not roll back the action it describes.

One schema inconsistency is mirrored rather than silently corrected: `adminUserId` is
`NOT NULL` but its foreign key is `ON DELETE SET NULL`, so deleting an administrator would
violate it. Fixing that needs a migration.

### Space feature gates — the contract disagreed with the caller
`gate()` returned `SpaceGateState.ALLOWED`, which reads like an authorization bypass. It
was not, in practice: `SpaceGateState` was a `StrEnum` while the caller constructs it with
keyword fields, so it raised `TypeError`, and the enum had no `CHAT_GROUP_CREATE` member.
Chat-group creation and group-session start both crashed.

Rewritten to the contract the callers use: a data object, a synchronous
`gate(feature, state)`, and `SpaceGateError` carrying `status_code`/`code`/`message`.
An unrecognised feature is denied rather than permitted.

> **Needs a product decision.** `FREE_CHAT_GROUP_LIMIT` and `FREE_GROUP_SESSION_LIMIT` are
> both `1`. These limits are documented nowhere in the repository or the specs, so they are
> a conservative starting point, not a recovered policy. They are isolated in one place so
> the numbers can be set once confirmed.

### Two stubs were shadowing working implementations
- `observation/tracker.record_activity` was `pass`, so sending a chat message never counted
  towards a study streak — while the analytics path called the real
  `progress/services/activity_tracker.record_activity` directly. Now delegates.
- `shared/infrastructure/socket_manager` defined a **second** `ConnectionManager` whose
  methods were `pass`. A full one already exists in `src/core/websocket.py`, and the
  course-generation service was already using it. **Two managers means two registries**, so
  a connection accepted through one is invisible to the other — which is exactly why chat
  messages went nowhere while course-progress updates arrived. Now re-exports the one
  instance, with `send_json` (payload-first) added to it as the alias ~20 chat call sites use.
  Also fixed: `manager.disconnect(...)` was called without `await` in two places, so
  connections were never removed from the registry.

### LLM stubs now fail informatively
`get_llm_router()` returned `None` and callers immediately call `.route_request()` on it.
It now names the missing subsystem instead. `FeatureFlagService.is_enabled` returned `True`
for every flag, turning an absent flag service into a blanket "yes" that could switch on
unfinished paths; it now **fails closed**.

`LlmService.generate` returned `""` and `generate_course_outline` returned `{}` — the latter
surfaced downstream as `ValueError("Outline contained no modules")`, blaming the model for a
method that was never written. Both are now implemented on top of `llm_resilient`, which
already does per-user provider selection across Gemini, OpenAI and Anthropic with circuit
breaking and cross-provider fallback. Course generation works again.

---

## 3. Paystack and referral rewards are non-functional

These two modules import now, but they cannot work: they still speak the **Prisma** client
API (`db_client.user.find_unique(where=...)`, 29 call sites) and read Prisma's camelCase
attributes (`user.referralCode`, `user.paystackSubscriptionCode`) off SQLAlchemy models.
`prisma` is not in `pyproject.toml`.

Rather than leave them dying on `NameError: name 'db' is not defined`, `db` is bound to a
`PrismaClientRemoved` sentinel that raises on first attribute access, naming the module, the
missing migration, and the fact that no data was read or written.

Stripe and Google Play are clean of Prisma and work.

**Cost to migrate**: ~29 call sites plus attribute renames across two files.
`referral_rewards_service` is 20 of them and is self-contained; `paystack_service` is 9.
The mapping is mechanical (`find_unique` → `select().where()`, `update` → `update()`), and
the `User` model already maps every column, so the attribute renames are one-to-one. Both
need integration tests against a real database, which is the larger part of the work.

**Decision needed**: Paystack matters for African markets. Is it in scope now, or is Stripe
sufficient for the current stage?

---

## 4. The largest remaining item: the LLM subsystem

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

This is what `get_llm_router()` needs, and it is why chat cannot produce a response.

**It also explains the skipped tests.** Nine of the files that never get collected are tests
for these exact modules: `test_circuit_breaker`, `test_cost_tracker`, `test_stream_normalizer`,
`test_tool_normalizer`, `test_openai_chat_tools`, `test_llm_agentic_roundtrip`,
`test_end_to_end_routing`, `test_feature_flags`, `test_llm_chat_context`. The tests were
written and are sitting there unused.

**Suggested sequence** (each step independently verifiable):
1. `types.py`, `protocol.py`, `capabilities.py`, `errors.py` — no dependencies.
2. `circuit_breaker.py` + `cost_tracker.py` — pure logic; unskips 2 test files.
3. `tool_normalizer.py` + `stream_normalizer.py` — pure transforms; unskips 2 more.
4. One provider adapter end to end (Gemini, since `gemini_sdk.py` already exists).
5. `router.py` — needs 1–4; unskips `test_end_to_end_routing`.
6. Remaining two adapters; unskips their test files.
7. `feature_flags.py` with real storage, replacing the fail-closed stub.

**Note on scope**: `llm_resilient.py` (already working, multi-provider) currently lives under
`domains/personal_learning/services`, which is the wrong home for a shared client. It is
deliberately left there: existing tests patch
`src.domains.personal_learning.services.llm_resilient.generate_content_json`, and moving it
would silently disable those patches. Relocating it belongs in step 5.

---

## 5. Remaining stubs

30 stub functions remain across 20 modules. They are no longer the dangerous kind — the ones
that corrupted data or bypassed a check are fixed. Most of what is left is the chat
subsystem, and restoring any of it individually achieves nothing while the router is
missing:

- `conversation/chat_greeting.py`, `chat_helpers.py`, `component_response.py`,
  `session_service.py` — chat message assembly
- `identity/onboarding.py` (4 functions) — conversational onboarding
- `action/action_service.execute` — tool dispatch
- `integrations/google_calendar/service.py` (3) — calendar sync; independent of chat
- `knowledge_base_service.index_user_uploads`, `kb_context_service` — retrieval, blocked on
  the vector-store decision in §6
- `billing/usage_tracking.emit_ai_usage` — usage rows; independent, small
- `ws_event_bus.publish_ws_event` — could now delegate to the consolidated socket manager
- `referral_service.get_daily_limit_increase` — returns `0` because the real implementation
  is one of the Prisma-era functions in §3

The three genuinely independent ones — `emit_ai_usage`, `publish_ws_event`, and the Google
Calendar trio — are small and can be done without waiting for anything else.

---

## 6. Database

Audited read-only against the live database (session-mode pooler; the direct
`db.<ref>.supabase.co` host is IPv6-only and unreachable from here).

| Check | Result |
|---|---|
| Orphaned `QuizSession.topicId` | **0** — safe to add a foreign key |
| `QuizSession` rows | 6 total, **0 with `topicId` set** |
| `ExamPrep.status` | `COMPLETED` 12, `SETUP` 8 |
| `PrepTopic.status` | `NOT_STARTED` 75, `MASTERED` 1 |
| `QuizSession.status` | `IN_PROGRESS` 5, `COMPLETED` 1 |
| `AuditLog` | exists, 7 columns, **0 rows** |
| `Embedding` | exists, **15 rows** |
| Applied revision | `012_add_practice_observations` |

No unexpected status values, so no enum widening is needed.

**Worth checking**: none of the 6 quiz sessions has `topicId` set. Those rows may simply
predate topic attribution, but it is worth confirming that `_resolve_topic_id` actually
persists, because per-topic mastery and therefore readiness depend on it.

### Two migrations written, neither applied

- **`013_add_quiz_session_topic_fk`** — adds the foreign key (`ON DELETE SET NULL`, so
  deleting a topic detaches attribution without destroying practice history) and an index on
  `topicId`. **Safe**: 0 orphans, column stays nullable. Additive only.
- **`014_drop_embedding_table`** — **destructive**. Deletes 15 rows. The table is
  unreachable by design: `vector` is `jsonb`, which Postgres cannot index or search by
  nearest neighbour, nothing writes it, Pinecone is gone, and `rag_service` reports
  `available = False`. `downgrade` restores the structure but **not the data**.

Neither has been run. Both need explicit go-ahead. If `014` is approved, the `Embedding`
model in `knowledge/db_models.py` should be deleted in the same change.

**When retrieval returns**, it should use `pgvector` in this same database so an embedding
and the row it describes commit in one transaction. That is a new table with a real vector
column, not a revival of this one.

---

## 7. Lint

`ruff` findings in `src` + `tests`: **556 → 16**.

The 567 fixed were mechanical: `Optional[X]` → `X | None`, import ordering,
`datetime.timezone.utc` → `datetime.UTC`. `openapi.json` is byte-identical afterwards,
confirming the rewrites were purely syntactic.

The 16 remaining are all naming style, no behaviour: 12 `N806` (uppercase locals that are
genuinely constants and would be better hoisted to module level), 2 `N811` (a WeasyPrint
import alias), 1 `N802`, 1 `N804`. `alembic/env.py` keeps 2 `E402` because Alembic requires
configuration before importing models.

---

## 8. Tests

**Collected: 799 passing, 10 skipped** (was 465 passing at the start of this pass).

Added:
- `test_module_imports.py` — 242 tests, one per module (§1)
- `test_email_infrastructure.py` — 33 tests
- `test_push_notifications.py` — 15 tests
- `test_restored_stubs.py` — 33 tests

**Skipped at collection: 24 → 21.** `conftest.pytest_ignore_collect` skips any file
containing `src.services.`, `src.routes.`, `src.core.database` or `src.schemas.subscription`.
Three were repointed and now run (11 tests): `test_cost_calculator`, `test_credit_service`,
`test_gemini_tool_handlers`.

The remaining 21 are **not dead and should not be deleted**:

- **9 are tests for the unmigrated LLM subsystem** (§4). They unskip as those modules land.
- **2 are Prisma-era** (`test_auth`, `test_password_reset`) and import a `db` global that no
  longer exists. They need rewriting, not repointing.
- **10 are partially migrated** — some imported symbols exist, some do not
  (`test_chat`, `test_chat_helpers`, `test_circle_billing`, `test_circle_gates`,
  `test_seat_service`, `test_usage_tracking_scope`, `test_subscription_catalog`,
  `test_moderation_service`, `test_circle_repository`, `test_chat_tool_arg_enrichment`).
  Each needs a per-file look; several are probably close to `test_credit_service`, which took
  one line.

Note `test_seat_service` imports 13 symbols from `src.services.seat_service` while a
migrated `seat_service` exists — a likely quick win, not attempted here.

Also note: CI sets `DATABASE_URL`, so the 10 tests skipped locally for lack of a database
**do run in CI**. Locally they need the pooler URL.

---

## 9. Still open, in priority order

1. **Confirm the two free-tier space limits** (§2) — a one-line change, currently a guess.
2. **Approve or reject the two migrations** (§6) — `013` is safe, `014` destroys 15 rows.
3. **Decide on Paystack and referral rewards** (§3) — ~29 call sites, or leave failing loudly.
4. **The LLM subsystem** (§4) — the single largest item; unskips 9 test files.
5. **Device-token registration** — until it exists, push cannot deliver. Tied to mobile scope.
6. **The three independent stubs** (§5) — `emit_ai_usage`, `publish_ws_event`, Google Calendar.
7. **Add `ruff` to CI** (§1) — prevents the lint backlog returning.
8. **The web Prepare surface is still 100% mocks.** Phases 5–6 of the integration plan are not
   started, and `apps/web/src/features/exam-prep/services/examPrepApi.ts` (449 lines) is dead,
   held alive only by type-only imports of `QuizMode` and `QuizQuestion` in two mock files.
   Moving those two types into the mocks unblocks its deletion.

## Not addressed, deliberately

- A single benign pydantic warning about an `alias="referralCode"` having no effect. It does
  not reproduce on direct import and both visible usages look correct. Pre-existing, and not
  proportional to chase further.
- Multi-worker websocket fan-out. The connection registry is in-memory, so with more than one
  worker a message reaches only the worker holding that user's socket. Needs a shared broker.
