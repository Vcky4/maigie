# Admin Dashboard — Plan

> Status: **Proposed.** Nothing in this document is built yet. The backend admin surface was removed during the SQLAlchemy migration and its router is unmounted; the admin frontend was kept and is largely complete but calls endpoints that no longer exist.
> Scope: the internal staff tool at `admin.maigie.com` — what it should do, how it should be built, and how it should behave given that it acts on learner data.
> Owners: Backend (admin domain, cross-domain reads, audit) + Admin client (`maigie-client/apps/admin`).
> Source of authority: the Maigie Book — `constitution`, `philosophy/ch04-product-principles`, `people/ch16-the-learner`, `intelligence/ch23-memory`, `engineering/ch48-technical-principles`, `business/ch36-pricing-philosophy`. Companion backend docs: [`LEARNING_INTELLIGENCE_DESIGN.md`](./LEARNING_INTELLIGENCE_DESIGN.md), [`MAIGIE_PLUS_COMMERCIAL_PLAN.md`](./MAIGIE_PLUS_COMMERCIAL_PLAN.md).
> **Where this plan and the book disagree, the book wins and this plan is wrong.**

---

## 1. Why this document exists

The admin dashboard is in an unusual state: the client exists and the backend does not.

`maigie-client/apps/admin` is a complete, separate Nx/Vite React app — roughly 25 pages, a grouped-nav layout, two-tier route protection, and a ~50-method `adminApi.ts` covering users, analytics, revenue, communications, content and system administration. It was retained through the migration.

The backend it talks to was not. `src/domains/admin/` still exists but is **unmounted** (`src/app.py`, `# --- Admin (pending SQLAlchemy migration) ---`), and what survives is a thin slice: `/health`, `/stats`, `/users` (list), user activate/deactivate, and a staff-role setter. Every other endpoint `adminApi.ts` calls — analytics, credits, subscriptions, feedback, careers, chat monitoring, referrals, finance, CMS, LLM config, system health — returns 404 today, because the router is off and most of those endpoints were never rebuilt.

So this is not a greenfield build and it is not a bug fix. It is a **reconciliation**: rebuild the backend the client already assumes, and — because the migration is a chance to do it once, correctly — reshape the features that no longer match the platform's model or its principles rather than restoring them as they were.

The user has explicitly asked that the dashboard be brought into line with the platform's philosophy and the book. That is the spine of this plan, not a footnote.

---

## 2. What the book decides for an internal tool

The dashboard is internal, but it acts on learners' accounts, money and memory. The same principles that govern the product govern the tool. These are constraints, not aspirations.

**Support, not surveillance.**
> A learner should never feel surveilled. / They should feel supported.
> — `people/ch16-the-learner`

An admin surface is where surveillance is easiest to build by accident. Any view of a learner's private activity (their AI conversations, their practice, their notes) has to justify itself against this line, expose the minimum needed for the task, and leave a record that it was accessed.

**Memory can be corrected and forgotten.**
> That it can be corrected. / That it can be forgotten when asked. … Without it, memory becomes surveillance.
> — `intelligence/ch23-memory`

Deleting a learner's account or data is a *request to forget*, and the tool must honour it as one — the `PracticeObservation` model already encodes this (cascade from `User`, `SET NULL` from a session). Admin deletion paths must respect the same cascade semantics rather than inventing harder-edged ones.

**Behaviour is understood, never judged.**
> Behaviour is not judged. / It is understood.
> — `platform/ch14-behaviour` (via `LEARNING_INTELLIGENCE_DESIGN.md`)

When the tool shows a learner's competence, hint usage or response times, it inherits the product's rule: these are signals, not demerits. "You were slow" is not something the product says, and it is not something an admin view should imply either.

**Every number traces to a persisted row.**
> No screen shows a learner a number about their own account that the server did not produce.
> — `MAIGIE_PLUS_COMMERCIAL_PLAN.md` §3

The honesty invariant applies to staff too. The dashboard shows no fabricated, estimated-and-unlabelled, or placeholder metric. If a number cannot be traced to a row or a defined computation, it does not ship. This directly rules out the vanity-metric wall an admin tool tends to become.

**Progress over activity; resist a single metric.**
> Activity is valuable only when it contributes to meaningful growth. (Constitution #6)
> It must resist the temptation to optimise toward a single metric. — `intelligence/ch24-reasoning`

> A dashboard shows information. / A Home welcomes you back.
> — `platform/ch07-personal-learning` (quoted in `LEARNING_INTELLIGENCE_DESIGN.md`)

The learner product is explicitly "not a dashboard." The staff tool *is* a dashboard, legitimately — but the same caution applies: prefer decisions and actions over counts, and insight over analytics for their own sake.

**Trust compounds.**
> Every interaction should strengthen confidence, transparency and respect. (Constitution #13)
> Earn Trust Every Day … transparency, privacy, reliability, fairness. — `philosophy/ch04`

Privileged actions must be auditable, attributable, reversible where possible, and least-privilege by default.

**The domain is the source of truth; simplicity is a feature.**
> When engineering decisions conflict with the domain model, the domain wins. — `engineering/ch48`
> A layer that does two things does neither well. — same

The admin domain is a **presentation and orchestration** layer, not a second data model. It should call each domain's services and repositories rather than re-querying their tables with its own raw SQL. See Decision 2.

---

## 3. Architectural decisions

**Decision 1 — Mount the admin router; build the backend to the client's contract.**
Uncomment the admin block in `src/app.py::_register_domains()` and treat `maigie-client/apps/admin/src/features/admin/services/adminApi.ts` as the de-facto contract to satisfy — except where §5 says a surface should change shape. The client is the spec; the book is the veto.

**Decision 2 — The admin domain orchestrates; it does not own a second copy of the data model.**
Cross-domain reads (users, courses, spaces, billing, usage, intelligence) go through each domain's own repository/service, not through raw `text()` SQL inside `admin/`. This respects bounded contexts (`ch48`), keeps the meaning of "is this learner Plus" in exactly one place (`entitlement_service.resolve`), and stops the admin tool drifting from the product's own numbers. Where a domain lacks the read it needs, add it to that domain and call it — the admin layer stays thin.

**Decision 3 — Every privileged action is audited. This is not optional and it already has a home.**
`AuditLog` exists in the database and `admin/services/audit_service.log_admin_action` is written and correct, but no endpoint calls it. Every mutating admin endpoint — deactivate, delete, credit/entitlement adjustment, role change, impersonation, config change, ledger edit — calls `log_admin_action` with before/after detail. The write is best-effort and never rolls back the operation it records (already designed that way). Audit logs are append-only and readable only by super admins.

**Decision 4 — The two-tier role must become real. Fix the default that makes every admin a super admin.**
`_get_staff_role` in `src/shared/auth/dependencies.py` currently returns `SUPER_ADMIN` for any `role=="ADMIN"` whose `adminStaffRole` is null, so `CONTENT_MANAGER` cannot actually be restricted. Tighten it to default null → `CONTENT_MANAGER` (least privilege) and require an explicit `SUPER_ADMIN`. Reconcile existing admin rows with a one-time backfill before this flips, so nobody is locked out. The client already encodes the intended split (`lib/adminRole.ts`, `superOnly` nav filtering, `SuperAdminRoute`); the backend must enforce the same line and never trust the client's gating alone.

**Decision 5 — "Credits" is a retired concept. The dashboard must speak the current commercial model.**
`adminApi.ts` still has `adjustUserCredits`, `resetUserCredits`, `updateCreditLimits`, `listSubscriptions`, `syncSubscription`. But `MAIGIE_PLUS_COMMERCIAL_PLAN.md` replaced credit caps with a **rolling usage window in cost units** plus **consumable Plus passes**, points, and **one resolver** (`entitlement_service.resolve`). The admin "Credits" surface is reshaped into an **Entitlements** surface: view a learner's current entitlement (source, why, until when) from `resolve`, their usage window and voice balance, their held/active passes, and their points ledger — and grant a comp pass or adjust the window through the billing domain's own service, audited. "Nothing is sold that is not enforced" applies in reverse here: the admin tool must not expose a lever the model no longer has.

**Decision 6 — Learner-private surfaces are minimised, purpose-bound, and audited.**
Chat monitoring, per-user analytics, and impersonation are the surfaces where the "never surveilled" principle bites. They are super-admin only, every access is audited (not just mutations), they expose the least that serves a defensible operational need (support, safety, abuse), and impersonation is a time-boxed, clearly-logged, revocable session — never silent. See §5 for per-surface treatment. This is the part of the plan most likely to need a product/policy decision before build (Open Question 1).

**Decision 7 — Marketing content is owned by the backend admin CMS. (Resolved.)**
`adminApi.ts` defines a full blog/jobs/content-calendar CMS under `/admin/content/*` with **no backing models in the backend**, while `maigie-public` also uses Keystatic (git-based CMS) for the same content. The decision is to **keep the backend admin CMS** as the owner: build the `BlogPost`, `JobPosting`, `CareerApplication` and content-calendar models and serve `/admin/content/*`. To honour "simplicity is a feature," Keystatic must not remain a second source of truth for the same content — the public site reads published content from the backend (via an API or a build-time pull), and Keystatic's role narrows to static/marketing-only pages or is retired for blog/jobs. The narrowing of Keystatic is a follow-up to confirm during Phase 3, not a blocker.

---

## 4. Current state — the contract gap

Grouped by the client's nav. **Backing** = does a backend model/service exist to serve it. **Fate** = restore / reshape / decide / new.

### Overview
| Client call | Endpoint | Backend today | Backing | Fate |
| --- | --- | --- | --- | --- |
| `getDashboardStats` | `GET /admin/dashboard` | absent (only `/stats`) | ✅ (identity, knowledge, spaces, intelligence) | restore, rename `/stats`→`/dashboard` or alias |
| `getDashboardCharts` | `GET /admin/dashboard/charts` | absent | ✅ derivable (signups, messages by day) | new (thin) |

### Users & Analytics
| Client call | Endpoint | Backing | Fate |
| --- | --- | --- | --- |
| `listUsers` / `getUser` / `createUser` / `updateUser` / `deleteUser` | `/admin/users*` | ✅ identity | restore; `delete` honours forget-cascade (Dec 6) |
| `getUserSummary` / `getUserAnalytics` | `/admin/users/{id}/summary`, `/admin/analytics/users/{id}` | ✅ but cross-domain | reshape — orchestrate domain services (Dec 2), judged-not framing (§2) |
| `activateUser` / `deactivateUser` | `/admin/users/{id}/(de)activate` | ✅ identity | restore + audit (Dec 3) |
| `getPlatformAnalytics` / `getEnhancedAnalytics` | `/admin/analytics`, `/analytics/admin/enhanced` | partial | reshape — progress-over-activity, honesty invariant |
| `getRevenue/Retention/Growth/UsersAtRisk` | `/admin/analytics/*` | derivable from billing/usage | new — read from `UsageEvent`/`PlusPurchase`, no fabricated numbers |
| `getReengagementAnalytics` / `wakeUser` / `wakeUsersBulk` / `bulkRegenerateSchedules` / deep-wake config | `/admin/analytics/reengagement`, `/admin/users/*wake*`, `/admin/retention/*` | partial (`home_service._check_re_engagement`) | reshape — "never guilt" re-engagement (§5) |

### Content
| Client call | Endpoint | Backing | Fate |
| --- | --- | --- | --- |
| `listAllCourses` / `getCourseDetails` / `deleteCourse` | `/admin/courses*` | ✅ knowledge | restore |
| blog / jobs / content-calendar CMS | `/admin/content/*` | ❌ no models | new backing (Dec 7 — backend owns it) |
| `listChatSessions` / `getChatStatistics` | `/admin/chat/*` | ✅ intelligence (ChatMessage) | reshape — surveillance-minimised, audited (Dec 6) |

### Revenue
| Client call | Endpoint | Backing | Fate |
| --- | --- | --- | --- |
| Credits: `adjust/reset/updateLimits` | `/admin/users/{id}/credits/*` | ❌ credits retired | **reshape → Entitlements** (Dec 5) |
| Subscriptions: `list/sync` | `/admin/subscriptions*` | passes/subscription in billing | reshape onto passes + `resolve` (Dec 5) |
| Finance ledger: currencies / fx-preview / CRUD | `/admin/finance/*` | ❌ no `LedgerLine` model | new backing domain (super admin) |
| Referrals: `list/stats` | `/admin/referrals*` | ✅ points ledger / referral rewards | restore onto points model |

### Communications
| Client call | Endpoint | Backing | Fate |
| --- | --- | --- | --- |
| Feedback: `list/get/update` | `/feedback*` | ❌ no Feedback model | new backing domain |
| Career applications: `list/get/update` | `/admin/career-applications*` | ❌ no model | new backing domain (paired with jobs CMS, Dec 7) |
| Bulk email | `/admin/bulk-email` | notifications infra exists | new — consent-aware, audited, rate-limited |

### System
| Client call | Endpoint | Backing | Fate |
| --- | --- | --- | --- |
| `getSystemConfig` / `updateSystemConfig` | `/admin/config` | config exists | new (careful surface — no secrets) |
| `getSystemHealth` | `/admin/system-health` | ✅ `check_db_health`, cache, workers | restore (extend `/health`) |
| `getLlmConfig` / `updateLlmConfig` | `/admin/llm-config` | provider config exists | new — audited |
| `getAiAgentTasks` / `getAiActionLogs` | `/admin/ai-agent-tasks`, `/admin/ai-action-logs` | ✅ `AIAgentTask`, `AIActionLog` (intelligence) | new (read-only) |
| Staff: `list/updateRole` | `/admin/staff*` | ✅ identity | restore + real role split (Dec 4) |
| Audit logs: `list` | `/admin/audit-logs` | ✅ `AuditLog` (written by Dec 3) | new (read) — super admin only |

Legend: ✅ backing exists · partial · ❌ needs a new model/domain.

---

## 5. Feature reshaping — where the book changes the design

**Entitlements (was Credits).** Replace the three credit endpoints with a single learner-entitlement read from `entitlement_service.resolve` (source, reason, expiry), plus usage-window and voice balances and the points ledger. Grants are comp passes issued through `pass_service`, audited. Rationale: credits no longer exist as a model; showing a credit lever is showing a promise the code cannot keep.

**Chat monitoring.** Reframe from "monitoring" to a **narrow, audited safety/support lookup**. Default view is aggregate statistics (volume, model mix, error rates) — no message content. Reading an individual learner's conversation is a distinct, super-admin-only, per-access-audited action with a stated reason, because `ch23` makes memory conditional on trust and `ch16` forbids the feeling of surveillance. Never a browsable feed of everyone's chats.

**Impersonation.** Keep it (support needs it) but make it honest: time-boxed token, unmistakably logged (who, whom, when, why), ideally signalled, and revocable. It is the sharpest trust instrument in the tool.

**Re-engagement / "wake".** The learner product's re-engagement is governed by a "never guilt" rule (`PERSONAL_LEARNING_USER_FLOW.md`, `home_service._check_re_engagement`). Admin-triggered wakes and bulk emails must route through the same copy discipline and consent/notification-preference checks — the tool must not become a back door around the product's own restraint. "Momentum over motivation," not nagging.

**Analytics.** Lead with progress and outcome signals (returning learners, learners achieving goals, mastery movement) over raw activity (messages, time). Every figure labelled and traceable (honesty invariant). No single headline metric to optimise (`ch24`). Revenue/retention/growth read from `UsageEvent` and `PlusPurchase`, not estimates.

**"Dashboard" naming.** Keep the word for the staff tool — it *is* a dashboard and the book's "not a dashboard" line is about the *learner* Home, not internal ops. But honour the spirit: bias the landing page toward "what needs a decision today" over a wall of counters.

**Dashboard composite — done (backend), verified against live data.** The landing page was empty because it reads one rich composite from `getDashboardStats()` (nested `retention`/`users`/`courses`/`chat`/`subscriptions`/`feedback`/`content`/`atRiskUsers`/`charts`), while the endpoint returned a flat six-count `/stats` alias — so every panel fell back to `|| 0`. `GET /admin/dashboard` now returns the composite from `admin/services/dashboard_service.overview()`, every field from real rows: retention (DAU/WAU/MAU/stickiness/at-risk) from `User.last_seen_at`, AI economics from the real `ChatMessage.cost_usd`/`revenue_usd`, revenue a genuine ~$0 until payment relationships exist (true, not fabricated), MRR an estimate off real tier counts × the published price (labelled `estimatedMRR`), satisfaction `null` when there are no responses (renders "—"). Also fixed the charts: `GET /admin/dashboard/charts` now emits `dailySignups`/`dailyMessages` with the `signups`/`messages` keys the chart reads (they were `count`, so the charts drew nothing). **Still a follow-up:** the deeper decision-led redesign (lead with what needs action, drop/curb activity-KPI tiles) is a frontend change in `maigie-client`; this makes the existing surface honest and populated rather than empty.

---

## 6. Phasing

Ordered so each phase is useful alone and nothing is built on a lever the model no longer has.

**Phase 0 — Foundation. ✅ Done (backend).** Admin router mounted in `src/app.py` (Dec 1). `_get_staff_role` now defaults a null `adminStaffRole` to `CONTENT_MANAGER`, so the two-tier split is real (Dec 4). `log_admin_action` is wired into deactivate/activate/staff-role, each recording before/after detail (Dec 3), and `GET /admin/audit-logs` (super-admin only, paginated, joined to the acting admin's email/name) reads the trail. `GET /admin/dashboard` added as the canonical alias of `/stats` (Dec 2 — orchestration pattern to deepen as domain reads are added). Verified: ruff clean, `create_app()` registers the admin routes, auth/admin test suite green. **Still owed before this is fully closed: Open Question 4** — reconcile existing `role=="ADMIN"` rows to explicit `SUPER_ADMIN` before the least-privilege default can be relied on in production, or current admins silently drop to content-manager access.

**Phase 1 — Users & entitlements (the core). ✅ Done (backend).** User management to the client contract: paginated `GET /users` (now lists everyone with `role`/`isActive`/`tier`/`search` filters, not just `role=='USER'`), `POST/GET/PUT/DELETE /users/{id}`, and `activate`/`deactivate` returning the full `UserAdminResponse`. `DELETE` reuses `identity.services.request_deletion` (the reviewed 90-day scheduled-deletion lifecycle) rather than a raw cascade — there is no hard-delete anywhere in the codebase, and "deletion is a request to forget" is honoured through that path. The **Entitlements** reshape (Dec 5) landed as `GET /users/{id}/entitlement` (tier/source/expiry from `entitlement_service.resolve`, usage window, voice via `voice_service.resolve`, points via `points_service.balance`, and held passes) plus an audited super-admin `POST /users/{id}/entitlement/grant-pass` that grants a comp pass into inventory via `pass_service.grant`. The retired credit fields are omitted. A compact `GET /users/{id}/summary` composes account facts + entitlement, framed as understanding not judgement. Every mutation is audited. Verified: ruff clean, 15 admin routes register, 250 tests pass. **Deferred to Phase 2:** `GET /admin/analytics/users/{id}` (`UserDetailAnalyticsResponse`) and platform analytics. **Client follow-up:** the admin Credits page still calls the retired `/credits/*` endpoints and needs repointing at `/entitlement`.

**Phase 2 — Analytics, honestly. ✅ Done (backend, honest subset). Verified against real data (1 212 users, 590 courses).** Grounding finding: no analytics service existed anywhere — built from scratch — and the client's `analytics.types.ts` is partly aspirational (server-side `UserAnalyticsResponse`/`CourseDetailResponse` were deliberately deleted for lacking backing). The honesty invariant governs what ships:
- **Shipped from real rows:** `GET /admin/analytics` (`AdminAnalyticsResponse`: platform stats — user/course/module/topic counts and splits, tier/difficulty breakdowns, AI-vs-manual, average course & per-user progress, estimated hours; plus top users, top courses, recent courses); `GET /admin/dashboard/charts` (daily signups from `User.createdAt`, daily messages from `ChatMessage.createdAt`); `GET /admin/analytics/users/{id}` (`UserDetailAnalyticsResponse`), framed as understanding not judgement.
- **Deliberately deferred, not fabricated:** the *enhanced* analytics (`/analytics/admin/enhanced`: session lengths, token/voice metrics, retention cohorts, LTV/funnel) has no backing data — shipping it would violate the honesty invariant, so it is left unbuilt with this note rather than filled with invented numbers. **Revenue/retention/growth** move to Phase 4 (Revenue ops): `PlusPurchase` gives honest revenue but only **per currency** (the code refuses FX conversion), and the client types assume a blended figure that would be a fabrication. **Re-engagement "wake"/send actions** move to Phase 3 (Communications): identifying at-risk learners is an honest read, but *acting* on it must route through the notifications orchestrator so `NotificationPolicy.engagement_enabled` + per-channel consent + quiet hours are enforced at send time ("absent consent is not consent"), and the copy stays guilt-free per the existing `_check_re_engagement` precedent.

**Phase 3 — Communications & content. 🔨 Re-engagement done; CMS/feedback/careers + bulk email pending (Phase 3b).**
- **Done (verified against real data — 1 203 at-risk learners):** `GET /admin/analytics/users-at-risk` (read-only, honest inactivity from `User.last_seen_at` + `UserStreak`, most-inactive first, described plainly not judged); `POST /admin/users/{id}/wake` and `POST /admin/users/wake-bulk` routed through the one orchestrator entrypoint `notifications.service.create_notification` (type `learning.gentle_return`), so `engagement_enabled` + per-channel consent + quiet hours + suppression are enforced at dispatch and a learner with engagement off receives nothing; the type's 14-day dedupe backstops nagging and the copy stays guilt-free. All audited.
- **Bulk email — deliberately not built as a raw broadcast.** `shared.infrastructure.email.send_bulk_email` sends immediately and checks neither consent nor suppression, so a mass send over it would bypass the entire consent architecture ("absent consent is not consent") and the book's trust rule. The consent-safe design is a fan-out over `create_notification` behind a new *announcement* notification type (category OPERATIONS/PRODUCT_UPDATES, EMAIL allowed, consent-gated). Deferred to 3b rather than shipped as a consent-bypassing broadcast.
- **Feedback — done (Phase 3b, verified against 4 live rows).** The `Feedback` table turned out to already exist (Prisma-era, with data, a `userAgent` column, timezone-naive timestamps and single-column indexes) — the same situation as `AuditLog`, so **no migration**: a model mirrors the live schema exactly (no `TimestampMixin`, naive `DateTime`, mirrored indexes) so autogenerate stays clean. Endpoints at `/api/v1/feedback`: learner `POST` (captures user-agent), staff `GET` list (paginated/filterable), staff `GET /{id}`, staff `PATCH /{id}` (stamps `resolvedAt` on RESOLVED, audited). The staff-only `get_feedback` is registered in `tests/test_owner_scoped_reads.py`'s allowlist — its authorisation is the role gate, not row ownership.
- **Careers — done (Phase 3b, verified with a live create→read→delete round trip).** As predicted, all four CMS/careers tables already exist (Prisma-era, empty): `BlogPost`, `JobPosting`, `CareerApplication`, `ContentCalendarEntry` — naive timestamps, `text[]` array columns — so again **no migrations**, models mirror the live schema. New `careers` domain: staff job CMS (`GET/POST /admin/content/jobs`, `GET/PATCH/DELETE /admin/content/jobs/{id}`), staff application triage (`GET /admin/career-applications` paginated, `GET`/`PATCH /admin/career-applications/{id}`, audited), and the public careers page it feeds (`GET /careers/jobs` published, `GET /careers/jobs/{slug}`, `POST /careers/applications` — captures user-agent/IP, requires the job to be published). No owner-scope allowlist needed — neither table has a `userId`.
- **Blog CMS — done (Phase 3b, verified with a live round trip).** New `content` domain mirrors the pre-existing `BlogPost` table (no migration): staff CRUD (`GET/POST /admin/content/blog`, `GET/PATCH/DELETE /admin/content/blog/{id}`, audited, slug-conflict → 409) and public reads (`GET /blog` published with category/search, `GET /blog/{slug}`). Tz-aware `publishedAt` is coerced to the column's naive form.
- **Content calendar CRUD — done (verified live).** `ContentCalendarEntry` mirrored in the `content` domain (no migration): `GET/POST /admin/content/calendar`, `PATCH/DELETE /admin/content/calendar/{id}`, audited. The **generate** (LLM → a `BlogPost`) and **cover-image upload** (object storage) actions are deliberately left unbuilt — each is a subsystem in its own right, and the admin client currently redirects its calendar page to the blog list, so nothing surfaces them yet.
- **Still deferred to 3b:** the consent-gated **announcement notification type** for bulk email, and the Keystatic narrowing (Dec 7).
- **Phase 3b content is otherwise complete:** feedback, careers, blog, and content-calendar CRUD all shipped by mirroring pre-existing tables — no migrations, Alembic head unchanged at 081 throughout. `/admin/analytics/reengagement`, `bulkRegenerateSchedules` and deep-wake config are schedule-regeneration concerns owned by personal-learning, deferred with them.

**Phase 4 — Revenue ops & system.**
- **Finance ledger — done (verified with a live round trip).** New `finance` domain mirrors the pre-existing (empty) `LedgerLine` table — no migration. Super-admin, audited: `GET /admin/finance/currencies`, `GET /admin/finance/fx-preview`, `GET /admin/finance/ledger` (paginated, with GBP totals `sumIncomeGbp`/`sumExpenseGbp`/`netGbp`/`avgMonthlyExpenseGbp` over the same filters), `POST/PATCH/DELETE /admin/finance/ledger/{id}`. **FX is manual and never fabricated:** GBP lines are 1:1 (`fxSource="same"`); a non-GBP line requires the operator to supply the GBP figure (`400` otherwise) and the per-unit rate is *derived*, not fetched — a live rate source can be layered in later behind the existing `gbpPerUnit`/`fxAsOfDate` columns. This is the one place a GBP base is correct: a books ledger reports in one currency, unlike commercial revenue (`PlusPurchase`), which stays strictly per-currency. Money is carried as strings to keep decimal precision.
- **Referrals — deferred (reshape).** `ReferralReward`/`ReferralRewardClaim` exist but are **empty**, and the model moved to `PointsLedgerEntry` (`kind='referral_qualified'`) per the commercial plan. An honest admin referrals view reads the points ledger, not the retired token tables — but the client's `ReferralReward` type is the old token shape, so this is a contract change (same situation as credits→entitlements), left for a reshape pass.
- **Subscriptions view/sync — deferred.** Should be reshaped onto `entitlement_service.resolve` + passes (Dec 5), and `sync` calls Stripe — out of proportion to this slice.
- **System reads — done (verified against 48 live `AIActionLog` rows).** Read-only, staff-gated, in the admin domain: `GET /admin/system-health` (the canonical name; reshape of the existing `/health` — db/cache/worker health), `GET /admin/ai-agent-tasks` and `GET /admin/ai-action-logs` (paginated, filterable, over the intelligence `AIAgentTask`/`AIActionLog` models).
- **System/LLM config — deferred.** `GET/PUT /admin/config` and `/admin/llm-config` are held back: the client's `SystemConfig` shape references the retired `creditLimits` model, the real `SystemConfig` table is a generic key/value store (`key`/`value`/`category`/`label`) that needs a reshaped contract, and config *writes* touch runtime settings and must be scoped to exclude secrets. A reshape pass, not a mirror.

**Phase 5 — Sensitive surfaces.** Chat lookup and impersonation, per Dec 6 and the resolution of Open Question 1 — deliberately last, because they need a policy decision, not just code.

---

## 7. Open questions

1. **Privacy policy for learner-private surfaces.** What operational justifications permit reading an individual's conversations or impersonating them, who may, and what is logged and surfaced to the learner? This is a policy decision the book constrains but does not settle. Blocks Phase 5.
2. ~~**CMS ownership.**~~ **Resolved (Dec 7): the backend admin CMS owns marketing content.** Follow-up during Phase 3: confirm how the public site consumes published content and what, if anything, Keystatic retains.
3. **Analytics scope.** Which progress/outcome metrics are worth deriving now versus after there is enough traffic (the commercial plan makes the same "a month of traffic, not a change" point about usage distribution)?
4. **Existing admin rows.** How many `role=="ADMIN"` users exist, and who should be `SUPER_ADMIN` vs `CONTENT_MANAGER` before Dec 4 flips the default? Needs a count against production, like `count_legacy_commercial_state.py` did for billing.

---

## 8. Sources

Book: `constitution.mdx`, `philosophy/ch04-product-principles.mdx`, `people/ch16-the-learner.mdx`, `intelligence/ch23-memory.mdx`, `intelligence/ch24-reasoning.mdx`, `engineering/ch48-technical-principles.mdx`, `business/ch36-pricing-philosophy.mdx`, `business/ch37-personal-learning.mdx`.
Backend: `src/app.py`, `src/shared/auth/dependencies.py`, `src/domains/admin/{routes,db_models}.py`, `src/domains/admin/services/audit_service.py`, `src/domains/billing/services/entitlement_service.py`, `src/domains/identity/db_models.py`, `src/domains/intelligence/db_models.py`.
Client: `maigie-client/apps/admin/src/app/app.tsx`, `components/layout/AdminLayout.tsx`, `features/admin/services/adminApi.ts`, `lib/adminRole.ts`.
Companion plans: `LEARNING_INTELLIGENCE_DESIGN.md`, `MAIGIE_PLUS_COMMERCIAL_PLAN.md`, `PERSONAL_LEARNING_USER_FLOW.md`.
