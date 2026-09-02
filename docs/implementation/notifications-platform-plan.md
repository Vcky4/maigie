# Notification platform

A single notification system for Maigie across in-app, mobile push, web push, and email — governed by learner consent, attention limits, delivery evidence, and an intelligence layer that improves timing and relevance without becoming the source of truth.

> Status: **Phases 0-3 implemented — 2026-09-01.** Contracts, per-channel and intelligence rollout controls, database-backed lifecycle observability, canonical in-app delivery, physically validated mobile push, the shared web/mobile settings matrix, ledgered learning/progress email with unsubscribe and bounce/complaint suppression, transactional delivery evidence, one consolidated transport, and daily/weekly digests are all in place. **Both external channels sit at 0% rollout with empty allowlists, so no learner is eligible until the cohort gate below is worked through** — and Resend webhooks must be registered before suppression can fire in a deployed environment. Phase 4's server side is complete and verified against PostgreSQL — VAPID configuration, subscription APIs, an aes128gcm sender, dispatch with expiry pruning, and a web push consent toggle in the settings matrix — with `WEB_PUSH_ENABLED` still `false` because the web client has no service worker yet. Next: the Phase 4 web client (manifest, service worker, permission UX, click routing), then the browser validation gate, then the deterministic intelligence baseline.
>
> Owners: Backend, web, mobile, product, design, data/ML, and operations.
>
> Scope: `/Users/vicksoson/projects/maigie`, `/Users/vicksoson/projects/maigie-client/apps/web`, and `/Users/vicksoson/projects/maigie-mobile`.
>
> Related work: [`adaptive-goal-lifecycle-plan.md`](./adaptive-goal-lifecycle-plan.md), which already creates several high-value notification events and fixed quiet-hour/daily-cap handling.
>
> Governing rule: every notification must originate in a real product event or an auditable intelligence decision. No channel sends directly from arbitrary feature code.

---

## 1. Purpose

Maigie already has fragments of a notification product:

- an in-app `Notification` record and unread APIs;
- a five-minute Celery delivery sweep;
- learner-local quiet hours and a daily interruption allowance;
- web and mobile notification centres;
- mobile Expo token registration and notification listeners;
- an FCM sender which cannot address the Expo tokens currently stored;
- SMTP and Resend email infrastructure, plus an older Brevo path;
- WebSockets, Redis, and a mature LLM routing layer.

The fragments do not yet form one system. Email bypasses the notification model, push transport and token format disagree, web push does not exist, preferences do not describe the real channel matrix, and no durable record can answer whether one channel was attempted, delivered, failed, retried, opened, or suppressed.

This plan makes notification creation, policy, delivery, interaction, and learning one platform. Feature domains publish intent; the platform decides whether, when, and where to deliver it; deterministic transports send it; every outcome is recorded.

## 2. Product principles

1. **The notification is not the push.** A notification is a durable user-facing message. In-app, email, web push, and mobile push are delivery channels for it.
2. **Domain facts stay deterministic.** A deadline, completed goal, new message, billing receipt, or security event comes from application state — never from a language model.
3. **Intelligence ranks and adapts; it does not invent obligations.** It may choose timing, channel, aggregation, and bounded phrasing. It may not manufacture facts, bypass consent, or silently suppress mandatory messages.
4. **Consent is checked at dispatch time.** A preference change after scheduling must take effect before any channel send.
5. **Quiet hours protect every interruptive channel.** In-app records may become visible without waking a device; push and optional email wait. Security and account-integrity notices follow an explicitly separate policy.
6. **No invisible loss.** Suppression, expiry, deduplication, provider failure, and exhausted retries are stored as outcomes.
7. **One interaction contract across clients.** A notification carries a versioned action, not separate free-form web and mobile routes.
8. **Attention is a budget.** The current daily cap becomes a per-user interruption budget spanning channels, not a count applied independently by every feature.
9. **Fail safe, not noisy.** Optional channel failures leave the in-app notification intact. Missing preferences fail closed for optional external delivery.
10. **Transactional and engagement communication are different classes.** Password resets, verification, receipts, and security warnings do not enter the same optimisation or unsubscribe rules as learning nudges.

## 3. Current state and concrete gaps

### 3.1 Backend

The existing implementation is in:

- `apps/backend/src/domains/personal_learning/services/notification_service.py`
- `apps/backend/src/domains/personal_learning/db_models.py`
- `apps/backend/src/shared/infrastructure/push_notifications.py`
- `apps/backend/src/shared/infrastructure/email.py`
- `apps/backend/src/domains/identity/db_models.py`
- `apps/backend/src/core/celery_app.py`

`create_notification` already defers during learner-local quiet hours, defers rather than drops after the daily allowance, exempts time-critical messages from that allowance, and expires stale queued messages. `deliver_pending` releases rows every five minutes and attempts push after marking the in-app row delivered.

The gaps are structural:

- `Notification` has one status for an operation that has many channel outcomes.
- `pushedAt` cannot represent attempts, multiple devices, retries, provider receipts, web push, or email.
- email sends happen through direct feature calls and a second Brevo task path, outside notification policy and observability;
- `DeviceToken` says FCM but stores Expo tokens and permits `WEB`, even though a web push subscription is not a token;
- mobile registers `ExponentPushToken[...]`, while the backend sender explicitly skips those tokens;
- there is no notification taxonomy registry, schema validation for `actionData`, idempotency key, dedupe window, grouping key, or expiry policy by type;
- preferences are a master boolean plus a few legacy booleans, while quiet hours and the daily cap live on a different profile table;
- there is no durable delivery-attempt ledger or interaction event ledger;
- there is no notification-specific intelligence service or feedback loop.

### 3.2 Web

The web client already has:

- `/notifications` and `NotificationsPage.tsx`;
- direct list/read/dismiss API calls;
- unused TanStack Query notification hooks with optimistic mutations;
- a notification action-route resolver;
- `LearningLayout` as the shell where a bell and live notification surface belong;
- a settings page with tab-based extension points;
- WebSocket clients, but no notification realtime client;
- a manifest, but no service worker, Push API integration, VAPID subscription, or PWA installation completeness.

The current endpoint returns an unpaginated unread list only. It cannot support notification history, cursor pagination, an efficient unread badge, mark-all-read, or category filters.

### 3.3 Mobile

The mobile app already has:

- `expo-notifications` configured;
- permission requests, Expo push token acquisition, and backend registration;
- foreground receive and tap listeners;
- an in-app notification centre with optimistic read/dismiss mutations;
- deep-link handling and pending-link infrastructure;
- Android `default` and `reminders` channels.

The gaps are concrete:

- the backend cannot send to the Expo tokens it receives;
- cold-start notification taps do not use `getLastNotificationResponseAsync`;
- unauthenticated notification taps bypass the pending-link flow and may be lost;
- push tap routing and in-app action routing use different type vocabularies;
- no badge reconciliation calls `setBadgeCountAsync`;
- token registration has no durable installation identity, token-change reconciliation, or retry queue;
- no notification preference screen exists.

## 4. Target architecture

```text
Domain change / scheduled evaluator / transactional command
                         |
                         v
              NotificationIntent
         (typed, versioned, idempotent)
                         |
                         v
          Notification Orchestrator
   + taxonomy + consent + policy + dedupe
   + attention budget + eligibility + timing
   + optional intelligence recommendation
                         |
                         v
              Notification record
            + planned channel rows
                         |
          Celery dispatch by due channel
          /          |          |          \
      in-app      mobile      web push      email
       store        Expo       Web Push   SMTP/Resend
          \          |          |          /
                         v
             DeliveryAttempt ledger
                         |
        delivered/opened/read/dismissed/clicked
                         |
                         v
            Interaction + outcome data
                         |
                         v
     bounded intelligence policy improvement
```

### 4.1 Domain ownership

Create a dedicated backend domain:

```text
apps/backend/src/domains/notifications/
  db_models.py
  models.py
  repository.py
  routes.py
  taxonomy.py
  policy.py
  orchestrator.py
  dispatcher.py
  intelligence.py
  events.py
  tasks/
    dispatch_due.py
    plan_engagement.py
    digest.py
    cleanup.py
```

Shared provider transports remain in `src/shared/infrastructure/` because authentication, billing, and other domains also need them. The notification domain owns policy and orchestration; transports only deliver a prepared payload and report a normalized result.

Feature domains must stop calling push/email infrastructure directly for product notifications. They emit a typed `NotificationIntent` or call the orchestrator. Authentication and security email can initially remain direct, then adopt the delivery ledger without adopting engagement optimisation.

### 4.2 Core data model

Retain and evolve `Notification` as the canonical in-app/user-facing object.

#### `Notification`

Add or standardize:

- `id`
- `userId`
- `type` — taxonomy key, for example `goal.at_risk`
- `schemaVersion`
- `category` — `SECURITY | ACCOUNT | BILLING | SOCIAL | LEARNING | PROGRESS | PRODUCT`
- `urgency` — `CRITICAL | HIGH | NORMAL | LOW`
- `title`
- `body`
- `action` — versioned canonical action object
- `sourceDomain`
- `sourceEntityType` / `sourceEntityId`
- `idempotencyKey` — unique per user and logical event
- `groupKey` — allows replacement or digest aggregation
- `createdAt`
- `eligibleAt`
- `expiresAt`
- `deliveredAt` — when visible in-app
- `readAt`
- `dismissedAt`
- `archivedAt`
- `status` — notification lifecycle only, not channel status
- `intelligenceDecisionId` — nullable trace to the decision record

Preserve `scheduledAt` during migration, then replace its meaning with `eligibleAt`. Preserve existing rows and API compatibility until every client has moved.

#### `NotificationDelivery`

One row per notification/channel destination:

- `id`, `notificationId`, `userId`
- `channel` — `IN_APP | MOBILE_PUSH | WEB_PUSH | EMAIL`
- `destinationId` — nullable installation/subscription/address reference
- `provider` — `internal | expo | web_push | smtp | resend`
- `status` — `PLANNED | SUPPRESSED | QUEUED | SENDING | ACCEPTED | DELIVERED | FAILED | EXPIRED | CANCELLED`
- `eligibleAt`, `nextAttemptAt`, `expiresAt`
- `attemptCount`, `maxAttempts`
- `providerMessageId`
- `suppressionReason`, `failureCode`, `failureDetail`
- `acceptedAt`, `deliveredAt`, `failedAt`
- timestamps

A provider accepting a request is not the same as a device displaying it. When a provider offers receipts, store the distinction. When it does not, report `ACCEPTED` honestly.

#### `NotificationDeliveryAttempt`

Append-only attempt evidence:

- delivery id and attempt number;
- request timestamp and duration;
- normalized provider response;
- retryability;
- provider message/receipt id;
- safe error code and redacted detail.

Do not store full provider payloads if they may contain personal learning content. Structured metadata is enough.

#### `NotificationPreference`

A normalized preference matrix, one row per user/category/channel with optional type overrides:

- `userId`
- `category`
- `notificationType` nullable
- `channel`
- `enabled`
- `frequency` — `IMMEDIATE | DIGEST | OFF`
- `digestPeriod` — `DAILY | WEEKLY` where applicable
- `updatedAt`

Global user policy remains separate:

- master engagement notification switch;
- timezone and source;
- quiet-hours start/end;
- interruption allowance;
- digest local time/day;
- preferred language.

Migrate legacy email and push booleans into this matrix, keep compatibility fields for one release, then remove them.

#### `PushInstallation`

Replace the overloaded `DeviceToken` concept:

- `id`, `userId`, stable `installationId`
- `platform` — `IOS | ANDROID | WEB`
- `transport` — `EXPO | FCM | APNS | WEB_PUSH`
- `token` nullable for mobile transports;
- web push `endpoint`, encrypted `p256dh`, encrypted `auth` fields;
- `appVersion`, `deviceLocale`, `timezone`, `permissionState`;
- `lastSeenAt`, `lastRegisteredAt`, `disabledAt`, `failureCount`;
- unique `(userId, installationId, transport)` and transport-specific uniqueness.

Web Push subscriptions are structured objects, not strings pretending to be mobile tokens.

#### `NotificationInteraction`

Append-only events:

- `notificationId`, nullable `deliveryId`, `userId`;
- `event` — `SEEN | OPENED | CLICKED | READ | DISMISSED | ACTIONED | UNSUBSCRIBED`;
- `surface` — `WEB | IOS | ANDROID | EMAIL`;
- `occurredAt`;
- optional action and source metadata.

This becomes the feedback source for intelligence and product analytics. Client-supplied events must be idempotent and authorized against notification ownership.

#### `NotificationDecision`

Audit intelligence recommendations without storing hidden chain-of-thought:

- input feature snapshot/version;
- policy/model version;
- candidate channels and times;
- chosen channels/time/grouping;
- reason codes and confidence;
- whether deterministic fallback was used;
- evaluation cohort/experiment id;
- cost and latency metadata where an LLM was used.

Store concise reason codes and outputs, not private model reasoning.

## 5. Notification doctrine, producers, taxonomy, and action contract

### 5.1 The book is a product requirement

This plan assumes the Maigie book is product source material. Notifications are one expression of the environment's **agency**: the environment remembers, notices, coordinates, encourages, and acts between explicit sessions. Their purpose is to protect **learning momentum**, not to maximize app engagement.

The governing chapters are:

- `maigie-book/content/product/ch32-notifications.mdx` — every notification is an interruption; notify to support learning; choose meaning over frequency; encourage without pressure; respect attention; know when to stay silent.
- `maigie-book/content/philosophy/ch03-a-new-model-of-learning.mdx` — a thinking learning environment welcomes, prepares, notices declining momentum, schedules revision, encourages discussion, celebrates growth, and surfaces help.
- `maigie-book/content/platform/ch07-personal-learning.mdx` — Personal Learning is organized around **Learn, Ask Maigie, Prepare, Organize, and Reflect**. These are contexts in which proactive help may arise; they are not five independent notification campaigns.
- `maigie-book/content/platform/ch12-progress.mdx` — notifications should support the loop from **activity to progress to achievement**. A send or open is not progress.
- `maigie-book/content/platform/ch14-behaviour.mdx` — sustainable consistency, returning after interruption, and healthy pauses matter more than pressure or raw streak preservation.
- `maigie-book/content/intelligence/ch24-reasoning.mdx` — intervention requires adequate evidence, uncertainty, humility, and deference to human judgment.
- `maigie-book/content/intelligence/ch25-agency.mdx` — reminders and educator alerts are permitted forms of agency, but only inside explicit scope, coordinated across agents, adaptive to preference, and subordinate to human choice.
- `maigie-book/content/people/ch21-the-support-network.mdx` — supporters may encourage or celebrate only within learner-controlled consent; struggle must not become surveillance.

The resulting non-negotiable rules are:

1. Every interruption must earn the right to exist.
2. Success is the meaningful activity the notification enables, not delivery, opens, clicks, screen time, or streak pressure.
3. Suggestions remain suggestions. The learner may act, choose another path, postpone, pause, dismiss, or revoke permission.
4. A missed day, one mistake, or one ignored message is not enough evidence for a behavioural inference.
5. Rest and deep concentration are protected states. Sometimes the correct decision is tomorrow; sometimes it is silence.
6. Ignore, dismiss, decline, snooze, pause, and unsubscribe are outcomes from which agency must become less intrusive.
7. No producer may independently escalate around the shared attention budget. Many features act through one intelligence and policy layer.
8. Computed interventions disclose their basis and uncertainty; they do not claim invented facts or diagnoses.
9. Private learning details are not disclosed to educators, peers, or supporters merely because a relationship exists.
10. Motivational copy recognizes verified effort or progress without guilt, fear, shame, or manufactured urgency.

The book does not define password reset, verification, billing, device security, provider mechanics, channel APIs, or exact TTLs. Those remain deterministic platform obligations and must not be represented as book-authored learning interventions.

### 5.2 What counts as a producer

A producer is the authoritative source of a `NotificationIntent`. Only these producer classes may create one:

| Producer class | What sends it | Fact authority | Intelligence authority |
| --- | --- | --- | --- |
| Domain event | A persisted state transition such as feedback published, achievement earned, invite created, or payment completed | The owning domain | May choose eligible timing/channel/copy; cannot change the event |
| User schedule | A reminder, study block, session, or checkpoint explicitly created/accepted by the user | The user's stated intention | May suppress redundant channels; cannot silently move the requested time |
| Scheduled evaluator | A deterministic recurring query over deadlines, due reviews, incomplete plans, or longitudinal behaviour | Stored application state plus versioned rules | May rank, group, choose silence, and recommend; hard policy validates the result |
| Async job completion | A requested document, course, export, generation, or processing job reaches terminal state | The job record | Normally none beyond channel selection |
| Social action | An authorized person sends, replies, mentions, invites, publishes feedback, or requests help | The persisted social/classroom action | May group and select timing; cannot invent a sender, recipient, or message |
| Transactional command/webhook | Identity, security, billing, membership, or legal state changes | The owning service/provider webhook | No suppression of mandatory delivery and no material generative copy |
| Intelligence opportunity | A versioned policy finds a useful, evidence-backed next action without a single triggering event | Auditable feature snapshot and decision record | May recommend only inside granted agency scope; silence is the default under uncertainty |

A UI component, page load, provider adapter, service worker, Expo listener, or email template is **not** a producer. It renders, transports, or reacts to a notification produced elsewhere.

### 5.3 What actually sends today

This is the implementation inventory as of 2026-08-29. “In-app works” means a durable `Notification` can be read by the clients. Its push side effect still cannot reach the Expo tokens currently registered by mobile.

#### Durable notification producers

| Current producer | Concrete trigger | Current type | Kind | Actual channels/status | Target action and gap |
| --- | --- | --- | --- | --- | --- |
| Streak milestone listener | `ProgressEvents.STREAK_UPDATED` reaches 7, 14, 30, 60, 100, or 365 | `celebration` | Verified behaviour recognition | In-app works; push attempted but transport-mismatched | No action; should open progress evidence |
| Achievement listener | `ProgressEvents.ACHIEVEMENT_UNLOCKED` | `celebration` | Verified achievement recognition | In-app works; push blocked by transport mismatch | No action; should open achievement |
| Topic-completed listener | `KnowledgeEvents.TOPIC_COMPLETED` | `suggestion` | Contextual learning opportunity | In-app works; push attempted | `generate_flashcards`; neither client consistently resolves it |
| Engagement sweep | Three or more declining days; every six hours | `ENGAGEMENT_NUDGE` | Computed momentum intervention | In-app works; push attempted | Web-shaped `/flashcards/due`; casing and action contract drift |
| Daily-plan task | Active learner profile; 06:00 UTC daily | `DAILY_PLAN` | Computed next-best-action summary | In-app works; push attempted | `/home`; type casing differs from both clients |
| Preparation review sweep | Preparation target passes; first ask plus bounded reminders | `preparation_review` | Learner reflection/decision | In-app works; push attempted | Preparation route works on both clients |
| Preparation result sweep | Missing outcome becomes eligible 14 days after sitting/review, max two reminders | `preparation_result` | Outcome/reflection follow-up | In-app works; push attempted | Mobile may route by `prepId`; web taxonomy/action support is incomplete |
| Study-plan check-in sweep | Opted-in plan has no check-in for seven days; daily sweep | `study_plan_check_in` | User-enabled reflection/checkpoint | In-app works; push attempted | Plan route works |
| Plan redistribution sweep | More than two overdue plan items and cooldown elapsed; 05:00 UTC daily | `study_plan_redistributed` | Deterministic plan-change notice | In-app works; push attempted | Plan route works |
| Goal lifecycle task | Course deadline is automatically extended | `goal_deadline_extended` | Deterministic material-change notice | In-app works; push attempted | Web goal route works; mobile lacks `goalId` rule |
| Goal lifecycle task | Overdue goal requires learner decision | `goal_needs_decision` | Time-bound learner decision | In-app works; push attempted | Web works; mobile action gap |
| Goal lifecycle task | Fixed deadline is near and goal is at risk | `goal_at_risk` | High-urgency computed intervention | In-app works; push attempted; priority 1 bypasses daily cap, not quiet hours | Web works; mobile action gap |

These producers live primarily in:

- `apps/backend/src/domains/personal_learning/events.py`
- `apps/backend/src/domains/personal_learning/tasks/`
- `apps/backend/src/domains/personal_learning/services/exam_prep_service.py`
- `apps/backend/src/domains/personal_learning/services/prep_outcome_service.py`
- `apps/backend/src/domains/personal_learning/services/study_plan_service.py`
- `apps/backend/src/domains/progress/services/goal_lifecycle_service.py`
- `apps/backend/src/workers/progress_tasks.py`

#### Direct channel producers that bypass the durable system

| Current producer | Trigger | Current kind/channel | Status and migration requirement |
| --- | --- | --- | --- |
| Identity service | Signup, verification, resend verification, forgot password | Verification/reset/welcome email | Working direct email; retain transactional policy, add delivery evidence |
| Learning Space service | Authorized invitation created | Space invitation email | Working direct email; migrate to `social.space_invite` with invite TTL and durable state |
| Stripe/Paystack fulfillment | Subscription activation webhook | Subscription-success email | Working direct email; normalize as billing/account event |
| Credit purchase fulfillment | Credit pack completes | WebSocket balance update, FCM push, email receipt | Email/socket work; Expo push does not; no canonical action |
| Credit consumption service | Free-tier balance exhausted, once per billing period | Limit-reached email | Working direct email; classify as account/billing guidance, not learning intelligence |
| Schedule reminder worker | Paid opted-in user's block starts in 15 minutes | Schedule-reminder email | Task is registered but no recurring schedule/caller was found; currently unreachable automatically |
| Weekly summary worker | Opted-in active user has meaningful weekly activity | Weekly summary email | Task is registered but no recurring schedule/caller was found |
| Intelligence email skill | User explicitly asks the agent to send email | User-commanded arbitrary email | Working direct email; it is an action skill, not proactive notification intelligence |

#### Partial, unreachable, fixture-only, or absent producers

- Retention evaluation calculates `feature_reminder`, `value_summary`, or `pause_offer` and records them as delivered, but does not send a notification and is not scheduled. It must not be treated as a working producer.
- `ClassroomEvents.SESSION_ENDED` and `DISCUSSION_CREATED` handlers only log.
- Web Classroom management advertises normal, notify, and urgent announcements, but it is a local design preview with no mounted backend producer.
- Classroom/Space UI promises assignment reminders, session reminders, direct messages, support check-ins, community health, new-member, educator-support, weekly digest, and Slack notifications without backend producers.
- Flashcard review-due, document/course job completion, reflection readiness, payment failure/refund, account deletion/security alerts, and admin broadcasts have no current notification producer.

#### Current vocabulary conflict

| Layer | Types it understands |
| --- | --- |
| Backend durable producers | `celebration`, `suggestion`, `ENGAGEMENT_NUDGE`, `DAILY_PLAN`, `preparation_review`, `preparation_result`, `study_plan_check_in`, `study_plan_redistributed`, `goal_deadline_extended`, `goal_needs_decision`, `goal_at_risk` |
| Web icon map | `nudge`, `streak`, `celebration`, `review_reminder`, `daily_plan`, `suggestion`, `exam_reminder`, preparation/study-plan/goal types; missing `preparation_result` |
| Mobile in-app icon map | Only `nudge`, `streak`, `celebration`, `review_reminder`, `daily_plan`, `suggestion`, `exam_reminder` |
| Mobile push-tap switch | `schedule_reminder`, `chat_message`, `course_update`, `streak_at_risk`, `reengagement` — none is emitted by the durable path |

This conflict is not cosmetic. It makes real notifications generic, inert, or incorrectly routed. Phase 0 must introduce one lowercase, namespaced taxonomy and versioned action union before new producers are enabled.

### 5.4 Target producer registry

Create `taxonomy.py` as the registry every producer and client contract is generated from or validated against. Each type defines producer class, audience, learning context, category, urgency, default channels, required action, TTL, dedupe/grouping, interruption-budget class, preference key, fact provenance, intelligence scope, and meaningful outcome.

#### User-requested and time-bound learning

These carry the strongest claim to interruption because they preserve explicit learner or peer commitments.

| Type | What sends it | Default kind/channels | Expiry | Intelligence boundary | Meaningful outcome |
| --- | --- | --- | --- | --- | --- |
| `learning.study_session_reminder` | User-created study block enters its reminder window | User-requested; in-app + push | Shortly after block end | May suppress redundant channel; cannot silently move time | Start, complete, snooze, or reschedule |
| `learning.revision_reminder` | User-requested concept/deck/course reminder becomes due | User-requested; in-app + push | When superseded or review becomes stale | Timing only inside chosen window | Start/complete review or reschedule |
| `learning.goal_checkin_reminder` | User-selected goal checkpoint becomes due | User-requested; in-app + push, optional digest | Next checkpoint | May group; cannot invent a checkpoint | Review goal and choose next step |
| `social.session_reminder` | Accepted collaborative/Classroom session enters reminder window | Commitment; in-app + push | Shortly after session begins | Channel/timing only | Join, decline, or reschedule |
| `classroom.assignment_due` | Incomplete assignment crosses configured deadline windows | External deadline; in-app + push, longer-lead email/digest | Deadline or grace end | May choose one eligible reminder window; deadline is fixed | Open, submit, or request allowed support |
| `classroom.assessment_upcoming` | Assessment approaches and learner is enrolled | External deadline; in-app + push, longer-lead email | Assessment end | May rank preparation action; facts fixed | Open/start preparation plan |

#### Deterministic product and domain events

| Type | What sends it | Default kind/channels | Expiry | Intelligence boundary | Meaningful outcome |
| --- | --- | --- | --- | --- | --- |
| `classroom.session_starting` | Scheduled Learning Session enters reminder window | Coordination; in-app + push | Shortly after start | Timing/channel only | Join or decline |
| `classroom.announcement_important` | Educator publishes to an authorized audience and marks importance | Information; in-app, push for important, email for urgent durable notice | Event-specific/pin end | Group/channel only; cannot alter message | Read/acknowledge/open reference |
| `classroom.educator_feedback_available` | Educator publishes persisted feedback | Feedback loop; in-app + push, email optional | Until superseded/reviewed | Privacy-safe preview and timing only | Read, reflect, revise, or resubmit |
| `learning.goal_deadline_changed` | Authorized deadline changes | Material plan change; in-app + push, email if consequential | When superseded | No fact generation | Review/accept revised plan |
| `learning.plan_redistributed` | Deterministic planner persists a materially changed plan | Material plan change; in-app + optional push | Next plan version | Explain changed facts; cannot claim user consent | Review/edit plan |
| `learning.resource_ready` | User-requested document/course/flashcards/reflection job succeeds | Async completion; in-app, optional push when user is waiting | Until opened/obsolete | No generative decision required | Open/use result |
| `learning.resource_failed` | User-requested async job reaches terminal failure | Async failure; in-app, optional push | Until retried/dismissed | Deterministic safe copy | Retry, inspect, or choose alternative |
| `social.space_invite` | Authorized Learning Space invitation is persisted | Invitation; in-app + email, push optional | Invite expiry | Timing/copy only | Accept or decline |
| `social.classroom_invite` | Authorized Classroom invitation/enrolment request is persisted | Invitation; in-app + email, push optional | Invite expiry | Timing/copy only | Accept or decline |
| `social.peer_response` | Peer/educator persists a reply to the learner | Conversation; in-app + grouped push | Thread relevance window | Group and privacy-safe preview only | Open/respond/resolve |
| `social.mention_or_direct_message` | Authorized person persists a mention/direct message | Directed communication; in-app + grouped push | Conversation window | Group/channel only | Open/respond |
| `social.contribution_recognized` | Verified answer/resource/help contribution reaches recognition rule | Contribution recognition; in-app, digest/opt-in push | Short | Bounded copy; fact fixed | View impact or continue contribution |

#### Intelligence-produced learning opportunities

These are the book's thinking-environment behavior. They require an auditable feature snapshot, agency permission, uncertainty, a silence option, and a closure action.

| Type | What sends it | Default kind/channels | Expiry | Intelligence boundary | Meaningful outcome |
| --- | --- | --- | --- | --- | --- |
| `learning.next_best_action` | Daily/home planner ranks eligible actions from goals, schedule, progress, and context | Guidance; Home/in-app first, push only inside protected study window | End of local day/context change | Rank, time, channel, or silence | Start recommendation or choose another action |
| `learning.review_due` | Retention evaluator finds due concepts/decks | Memory support; in-app + grouped push, digest for backlog | Review/recomputation | Group, rank, time; due facts fixed | Complete review and record result |
| `learning.unfinished_work_ready` | Meaningful incomplete work and a suitable return window coexist | Continuity; in-app, optional bounded push | Context change | Rank/time/silence; repeated dismissal suppresses | Resume, archive, or defer |
| `learning.momentum_support` | Multiple longitudinal signals show declining momentum; one missed day is insufficient | Support; in-app first, at most one opt-in push | Recovery/recomputation, max 24h | Decide silence/timing/channel/supportive template; disclose basis | Resume, re-plan, ask for help, or choose pause |
| `progress.goal_at_risk` | Goal lifecycle evaluator finds auditable pace/risk evidence near a fixed deadline | Time-bound support; in-app + push | Goal deadline or recovered state | Rank timing/channel and explain evidence; cannot alter the fixed date | Review plan, begin work, or request support |
| `progress.goal_decision_required` | Goal becomes overdue or infeasible and no safe automatic adjustment is allowed | Learner decision; in-app + push, email only for durable long-lead decision | Resolution or superseding goal version | Present bounded options; cannot decide for the learner | Complete, reschedule, deprioritize, or abandon explicitly |
| `learning.plan_adjustment_suggested` | Feasibility, deadline, schedule, or behaviour changes materially | Recommendation; in-app, push only when decision expires | Plan version/deadline | Propose and explain; never auto-apply without granted scope | Accept, edit, or reject |
| `learning.reflection_opportunity` | Meaningful session, mistake, milestone, or changed pattern completes | Reflection; in-app/digest | Short contextual window | Choose useful moment/question from approved forms | Reflect or explicitly skip |
| `learning.break_recommended` | Current active-session pattern indicates rest may be healthier | Healthy pause; active in-app UI only | Immediate | Cautious suggestion; never external push/email | Take break, continue, or dismiss |
| `learning.preparation_opportunity` | Upcoming exam/interview/presentation/project lacks sufficient planned preparation | Preparation guidance; in-app + optional push | External event | Rank next preparation action; date fixed | Create/start plan |
| `learning.resource_recommended` | High-confidence resource matches active work, discussion, or gap | Discovery; in-app/digest | Context change | Rank and explain relation; no deep-focus interruption | Save, open, discuss, or dismiss |
| `social.help_opportunity` | An unanswered request matches a learner's demonstrated context and consent | Peer learning; in-app/digest | When answered | Match privately; never expose inferred weakness | Offer help, answer, or decline |
| `social.collaboration_suggested` | Shared objective/complementary strengths and consent make collaboration useful | Opportunity; in-app/digest | Short | Suggest only; never auto-pair or disclose struggle | Invite, accept, or decline |
| `educator.learner_support_needed` | Multi-signal evidence indicates timely human support may help | Educator intervention; in-app + bounded escalation | Review/state change | Rank with evidence, uncertainty, fairness checks; never diagnose | Educator reviews and records support action |
| `educator.classroom_intervention_needed` | Aggregate evidence shows confusion, stalled progress, or teaching/resource opportunity | Classroom insight; in-app/digest | New evidence | Aggregate and summarize; educator decides | Create intervention/resource/session |
| `space.community_health_attention` | Aggregate collaboration slows or unanswered questions accumulate | Leader insight; in-app/digest | Recovery/new evidence | Preserve privacy; no unsupported causal claims | Leader starts support/discussion/session |
| `support.encouragement_requested` | Learner-granted support policy says encouragement would help | Consent-scoped support; supporter in-app/email/opt-in push | Short | Reveal minimum; may choose silence only within granted scope | Supporter encourages; learner may revoke |

#### Progress, return, and discovery

| Type | What sends it | Default kind/channels | Expiry | Intelligence boundary | Meaningful outcome |
| --- | --- | --- | --- | --- | --- |
| `progress.activity_milestone` | Verified difficult or sustained learning activity reaches a milestone | Recognition; in-app, occasional opt-in push/digest | Short | Group/time/copy; no exaggeration | Reflect or continue intended work |
| `progress.learning_improved` | Auditable understanding, retention, confidence, or consistency improves materially | Progress recognition; in-app/digest | Context window | Summarize only measured basis | Reflect/set next action |
| `progress.achievement_earned` | Course, exam, certification, goal, or defined achievement completes | Achievement; in-app, opt-in push/email/share | Medium | Copy/channel only; achievement fixed | View, reflect, or share by choice |
| `progress.contribution_milestone` | Verified peer teaching/help contribution reaches milestone | Community recognition; in-app/digest | Short | Copy/grouping only | View impact or continue contribution |
| `learning.gentle_return` | Meaningful absence plus unfinished learner-owned objective and prior permission | Return support; in-app plus one opt-in push/email | One attempt/context change | Strong silence and fatigue authority; never guilt | Resume, re-plan, pause, or disable |
| `discovery.opportunity` | High-confidence contextual person, course, Classroom, resource, idea, or forgotten concept is useful | Discovery; in-app/digest | Short | Rank/contextualize; never click bait | Read, practise, discuss, teach, reflect, or save |

The old `engagement.reengagement` type is intentionally replaced by `learning.gentle_return` and `learning.momentum_support`. The book treats return, continuity, consistency, and healthy pause as learning states—not marketing engagement states.

#### Account, security, billing, membership, and operations

These are required platform notifications but are not derived from the book's learning doctrine. Their owning command or verified webhook is the sole fact producer; intelligence cannot suppress mandatory sends or generate material facts.

| Type | What sends it | Default channels | Meaningful closure |
| --- | --- | --- | --- |
| `security.account_alert` | Verified suspicious/significant account-security event | Email + in-app + available push according to security policy | Review and secure account |
| `account.verification` | Signup/email verification command | Email | Verify identity |
| `account.password_reset` | Authorized reset request | Email | Reset or ignore safely |
| `account.identity_changed` | Email/password/provider identity changes | Email + in-app | Confirm or report |
| `account.data_export_ready` | Export job completes | Email + in-app | Download before expiry |
| `account.deletion_scheduled` | Deletion command enters recoverable period | Email + in-app | Confirm, cancel, or await |
| `billing.receipt` | Verified payment fulfillment | Email + in-app | View receipt; no action required |
| `billing.payment_failed` | Verified provider failure requiring action | Email + in-app, optional push | Repair payment |
| `billing.subscription_changed` | Verified activate/cancel/pause/renewal state change | Email + in-app | Review state |
| `billing.credit_balance_changed` | Verified credit purchase/refund/adjustment | In-app + realtime; email receipt where monetary | Review balance/receipt |
| `membership.role_or_access_changed` | Authorized Learning Space/Classroom membership transition | In-app + email, optional push | Review new access or report |
| `operations.incident_notice` | Authorized staff incident workflow | In-app/email/push according to impact | Read status/instructions |

### 5.5 Channel meaning under the book

- **Home/in-app** is canonical for low-urgency guidance, next-best-action, reflection, discovery, progress, and opportunities. The Home answers what matters now without demanding interruption.
- **Push** is reserved for expiring coordination, exact user reminders, directed social responses, material time-bound changes, and bounded support where delay reduces learning value.
- **Email** is for durable transactional obligations, invitations, significant longer-lead changes, support-network communication, and user-chosen digests. The book does not prescribe email; this is the channel implementation of durability and attention principles.
- **Digest** collects low-urgency recommendations, discovery, recognition, educator insights, and community health. It must not become a dump of unread notifications.
- **Silence** is an explicit dispatch result with a reason, not a failure to decide.

### 5.6 Canonical action and outcome contract

Replace free-form `actionData.route` with a versioned action union, for example:

```json
{
  "version": 1,
  "kind": "OPEN_GOAL",
  "entityId": "goal-id"
}
```

Clients map `kind` to their own valid routes. The backend never writes a React route or Expo route. Unknown actions still open the notification detail/centre instead of failing.

Every taxonomy entry also declares an outcome contract. Delivery may be operational success; `OPENED`, `READ`, and `CLICKED` may be useful intermediate events; none is automatically the learning outcome. The outcome is the intended activity: start or complete review, join a session, submit work, review feedback, reflect, accept/reject a plan, provide/receive help, repair payment, pause, or explicitly decline. The intelligence layer optimizes meaningful action per interruption and learns equally from action, alternative choice, postponement, dismissal, pause, and revocation.

## 6. Orchestration and deterministic policy

### 6.1 Ingestion

The orchestrator accepts `NotificationIntent`:

```text
user_id
notification_type
idempotency_key
source entity
facts used to render
optional requested eligibility/expiry
optional policy context
```

It then:

1. validates the taxonomy and action schema;
2. enforces idempotency and dedupe/grouping;
3. renders deterministic baseline content;
4. resolves timezone, preferences, and available destinations;
5. applies mandatory/transactional policy;
6. calculates quiet hours, attention budget, and expiry;
7. optionally requests an intelligence recommendation for eligible categories;
8. validates that recommendation against hard constraints;
9. creates the `Notification` and channel delivery rows atomically;
10. schedules due work through Celery.

### 6.2 Attention budget

Count interruptive deliveries, not in-app records. A notification sent to mobile push and email should not consume two unrelated feature caps without an explicit policy.

Suggested initial budget:

- security/account integrity: outside engagement budget;
- user-requested exact reminders: reserved budget;
- high-value deadline interventions: at most 2/day;
- general learning/progress nudges: at most 3/day;
- recognition/discovery/gentle-return interventions: at most 1/day and suppressible;
- total engagement interruptions: default 5/day.

Start rule-based. Intelligence may choose among remaining candidates, never expand the budget.

### 6.3 Idempotency, dedupe, and grouping

- Producers must provide stable idempotency keys.
- `goal.at_risk:<goal_id>:<risk_state_version>` is one logical event even if a task retries.
- Repeated message notifications group by conversation and update a count.
- Review reminders group by due window.
- A materially changed fact may replace an unread grouped notification rather than append noise.
- Database uniqueness, not Redis alone, is the final idempotency boundary.

### 6.4 Retry and expiry

- Provider/network failures use exponential backoff with jitter.
- Invalid destinations are disabled, not retried indefinitely.
- Retry only while `now < expiresAt`.
- Permanent content/payload errors dead-letter immediately.
- Celery tasks operate per delivery or bounded batch and are idempotent.
- Reconciliation jobs recover rows left in `SENDING` after worker loss.

## 7. Intelligence layer

“Powered by intelligence” should mean measurable personalisation under hard product constraints, not an LLM making every send decision.

### 7.1 Responsibilities

The intelligence layer can recommend:

- **priority/ranking:** which eligible intervention has the highest expected learner value;
- **send time:** a time window based on local study behaviour and prior engagement;
- **channel:** in-app only versus one interruptive channel based on availability and historical response;
- **grouping/digest:** combine low-value candidates into one useful summary;
- **fatigue control:** reduce low-value sends after repeated dismissal/non-response;
- **bounded copy variation:** choose among approved templates or fill tightly constrained phrasing slots;
- **escalation:** move a genuinely time-sensitive goal event from in-app only to push, within policy.

It cannot:

- change a deadline, score, payment, entitlement, or security fact;
- opt a user into a channel;
- send during prohibited quiet hours;
- exceed category or global frequency caps;
- convert marketing into transactional communication;
- select an action unsupported by the taxonomy;
- suppress mandatory security/account messages;
- make delivery depend on a live model call.

### 7.2 Three maturity levels

#### Level 0 — deterministic baseline

Ship first. Rules use timezone, quiet hours, urgency, user preferences, existing study times, deadline distance, recent notifications, and available destinations. This is the control and fallback forever.

#### Level 1 — statistical ranking

Use interpretable features and a versioned scoring model to rank candidates and select time/channel. Start with offline evaluation against interaction events, then shadow mode, then a small experiment. A contextual bandit is appropriate only after reliable exposure and outcome data exists.

#### Level 2 — LLM-assisted planning and copy

Use the existing LLM router on the `heavy` queue only for non-urgent, precomputed work such as weekly digest summaries or selecting an approved tone variant. Require structured JSON, validate against Pydantic schemas, record model/cost/version, and fall back to deterministic copy.

A model call must never sit in the five-minute dispatch path. Generated content is stored and reviewed by policy before a delivery becomes due.

### 7.3 Features and labels

Initial non-sensitive features:

- learner-local hour/day;
- preferred study windows and actual session history;
- recent notification counts by category/channel;
- recent opens, dismissals, action completion, and time-to-action;
- goal deadline distance and deterministic risk state;
- active platform and destination availability;
- coarse app recency;
- whether similar content is already visible in-app.

Avoid raw document content, private chat text, protected characteristics, and unnecessary demographic inference.

Labels must distinguish:

- provider acceptance;
- notification open/read;
- meaningful product action within an attribution window;
- dismissal/unsubscribe;
- no response.

An open is not automatically success. For `goal.at_risk`, the meaningful outcome may be plan review or completed work. For a reminder, it may be starting the scheduled session.

### 7.4 Evaluation and guardrails

Primary success measures:

- meaningful action rate per interruption;
- notification-assisted task completion;
- reduced overdue/review backlog;
- unsubscribe and permission-revocation rate;
- dismiss rate and notification fatigue;
- delivery success and latency.

Guardrail measures:

- sends during quiet hours;
- preference violations;
- duplicate sends;
- daily-budget violations;
- provider complaint/bounce rates;
- category-level fairness and performance drift;
- LLM schema failure, latency, and cost.

Roll out via shadow recommendations, then 5%, 25%, 50%, and 100% only when guardrails remain healthy. Every intelligence feature has a kill switch and deterministic fallback.

## 8. Channel implementation

### 8.1 In-app

In-app is the canonical default for engagement notifications.

Backend APIs:

- `GET /api/v1/notifications?status=&category=&cursor=&limit=`
- `GET /api/v1/notifications/unread-count`
- `POST /api/v1/notifications/{id}/read`
- `POST /api/v1/notifications/{id}/dismiss`
- `POST /api/v1/notifications/read-all`
- `POST /api/v1/notifications/{id}/interactions`

Return a cursor-paginated envelope with `items`, `nextCursor`, and `unreadCount`. Keep old `/learning/notifications` routes as temporary adapters.

Publish a compact `notification.created`, `notification.updated`, and `unread_count.changed` event over the existing authenticated WebSocket infrastructure. Polling remains the fallback; reconnect always invalidates/refetches, so socket loss cannot corrupt state.

### 8.2 Mobile push

**Decision: use Expo Push Service for the current Expo application.** The client already registers Expo tokens and EAS manages APNs/FCM credentials. Adding an Expo transport is the shortest reliable path and keeps native provider credentials out of application code. Keep the FCM sender only for future native FCM tokens and explicit callers.

Backend:

- add an Expo sender with chunking, request tickets, receipt polling, retry classification, and `DeviceNotRegistered` pruning;
- store ticket ids on delivery attempts;
- never mix Expo and FCM tokens in one sender;
- include only the canonical action and safe identifiers in data payloads;
- use Android channel ids from taxonomy;
- send badge values based on canonical unread count where supported.

Mobile:

- generate and persist a stable installation id;
- upsert installation on login, token change, app update, timezone/locale change, and permission change;
- disable the installation on logout without deleting evidence;
- unify tap routing through the canonical action resolver;
- process cold starts with `getLastNotificationResponseAsync`;
- pass unauthenticated taps through `pendingLink`;
- reconcile app icon badge from unread count on receive, read, dismiss, foreground, and login;
- record `OPENED`/`ACTIONED` interactions idempotently.

Do not request push permission during onboarding without context. Ask after a user creates a reminder, schedule, goal, or other feature whose value makes the request understandable.

### 8.3 Web push

Web push is net-new PWA infrastructure.

Backend:

- configure `WEB_PUSH_VAPID_PUBLIC_KEY`, `WEB_PUSH_VAPID_PRIVATE_KEY`, and subject;
- store subscription endpoint and encrypted key material;
- send standards-based Web Push payloads;
- disable subscriptions on HTTP 404/410;
- expose authenticated subscribe/unsubscribe/status endpoints;
- rate-limit registration and validate endpoint schemes/size.

Web:

- add a service worker under the app scope;
- complete manifest icons and metadata;
- register the service worker only in supported production-like browser contexts, excluding prerender execution;
- convert the VAPID key safely and call `pushManager.subscribe` after explicit user intent;
- POST the subscription from the page, where the access token is available; the service worker must not read localStorage;
- handle `push` and `notificationclick` events;
- map canonical actions to same-origin routes and reject arbitrary URLs;
- expose permission state and recovery instructions in settings.

Web push is unsupported on some browser/platform combinations and requires installation on some iOS versions. The UI must describe availability instead of presenting a broken universal toggle.

### 8.4 Email

Consolidate on `src/shared/infrastructure/email.py` and its SMTP/Resend provider chain. Retire or adapt the separate Brevo notification path so provider selection is one operational concern.

- transactional auth/security/receipt emails remain immediate and deterministic;
- learning/progress email flows through notification preference, quiet-time, digest, and delivery records;
- templates have HTML and plaintext variants;
- every message has a stable entity reference and provider id;
- include category-appropriate unsubscribe/preference links for non-transactional email;
- process provider webhooks for delivered, bounced, complained, and opened/clicked where privacy settings and provider semantics permit;
- sign and replay-protect webhooks;
- suppress hard-bounced/complaining destinations globally;
- never place private learning details in email subjects or lock-screen previews by default.

Digest email is a separate notification type built from eligible items; it is not a loop that blindly sends every unread notification.

## 9. Preferences and user experience

One preference model drives web, mobile, and backend. Both clients render the same category/channel capabilities returned by the API.

Endpoints:

- `GET /api/v1/notification-preferences`
- `PUT /api/v1/notification-preferences`
- `GET /api/v1/push-installations`
- `DELETE /api/v1/push-installations/{id}`
- `POST /api/v1/push-installations/mobile`
- `POST /api/v1/push-installations/web`

Settings UI:

- master engagement notification control;
- channel controls for mobile push, web push, and email;
- category rows with immediate/digest/off where supported;
- timezone and quiet hours;
- daily interruption allowance with sensible bounds;
- digest schedule;
- current device/browser permission and registered-installation state;
- explicit note that security/account messages cannot be disabled through engagement controls.

Defaults must preserve existing users' effective choices. Do not reinterpret a legacy `true` as consent for new categories that did not exist when it was captured.

## 10. Web implementation plan

1. Move notification API and hooks into `features/notifications/`; promote query keys into a notification namespace.
2. Move `NotificationsPage` from direct `useEffect` calls to TanStack Query hooks.
3. Add cursor pagination, history filters, unread count, mark-all-read, and action handling.
4. Add a notification item component shared by page and panel.
5. Add a sidebar/mobile-header bell with unread badge; suppress shell chrome in immersive routes consistently.
6. Add a lightweight live notification surface mounted in `LearningLayout`, without inventing a generic toast system that bypasses notification policy.
7. Add the authenticated notification WebSocket client with polling/refetch fallback.
8. Add `NotificationSettings` to `SettingsPage`.
9. Add service worker, web push registration, permission UX, and canonical click routing.
10. Add interaction reporting and accessibility: focus management, keyboard navigation, screen-reader labels, reduced motion, and non-color read state.

## 11. Mobile implementation plan

1. Extract notification data and route logic from `features/home` into `features/notifications`.
2. Define one canonical action-to-route resolver and route validation tests; remove the competing type switch in `_layout.tsx`.
3. Add cold-start tap retrieval and pending-link auth handling.
4. Add stable installation identity and reliable token/permission reconciliation.
5. Add unread badge reconciliation.
6. Add notification preferences under profile/settings using the shared API contract.
7. Record open and action interactions.
8. Add foreground/background refetch reconciliation.
9. Preserve accessibility constraints in the current notification centre.
10. Validate physical-device Android and iOS delivery in EAS preview builds; simulator success is not push evidence.

## 12. Security, privacy, and abuse controls

- Authenticate every preference, installation, notification, and interaction endpoint.
- Authorize every notification operation by `userId`; never accept a user id from the client for ownership.
- Encrypt web push key material at rest and redact tokens/subscriptions from logs.
- Treat provider payloads as externally observable. Put only minimal data in push.
- Restrict actions to a server-defined enum and client allowlist; no arbitrary external URL execution.
- Rate-limit installation registration, interaction events, test notifications, and preference writes.
- Add CSRF/replay protection and signature verification for provider webhooks.
- Separate transactional lawful basis/requirements from optional engagement consent.
- Support account deletion by removing active destinations and personal interaction data according to retention policy.
- Define retention: attempts and operational logs short-lived; aggregate analytics longer-lived and de-identified.
- Add staff controls with audit logs for broadcasts. No arbitrary production broadcast endpoint in the first release.
- Prevent one tenant/space's title or content leaking to another user's device through strict recipient derivation and ownership tests.

## 13. Observability and operations

Structured metrics:

- intents accepted/rejected/deduplicated;
- planned/suppressed by reason, category, and channel;
- queue depth and oldest due age;
- attempts, acceptance, delivery, permanent failure, and retry rates;
- provider latency and error codes;
- unread backlog age;
- opens, actions, dismissals, unsubscribes;
- quiet-hour and budget violation counters (expected zero);
- intelligence fallback, schema failure, cost, and decision latency.

Dashboards:

1. platform health by channel/provider;
2. policy health and consent violations;
3. user engagement and fatigue;
4. intelligence experiment performance;
5. dead-letter and stale-queue operations.

Alerts:

- provider acceptance drops below threshold;
- queue age exceeds dispatch SLO;
- duplicate or budget-violation counter is nonzero;
- bounce/complaint spike;
- receipt worker stops progressing;
- intelligence fallback or cost spikes.

Operational tools should allow authorized staff to inspect a notification's lifecycle and retry a safe failed delivery. They must not expose full private content unnecessarily.

Initial SLOs:

- 99% of due high-urgency dispatches attempted within 2 minutes;
- 99% of normal dispatches attempted within 5 minutes;
- unread count converges within 10 seconds while connected and on next poll otherwise;
- zero sends against an explicit disabled preference;
- zero engagement pushes during quiet hours;
- duplicate external delivery below 0.01%.

## 14. Rollout phases

### Phase 0 — contracts and measurement foundation

Backend:

- [x] Establish taxonomy, action union, category, urgency, TTL, and dedupe policy — **foundation added 2026-08-29; producers still use legacy types until migrated.**
- [x] Add `NotificationDelivery`, `NotificationDeliveryAttempt`, `NotificationInteraction`, `NotificationDecision`, and `PushInstallation` migrations — **added in revision 058 on 2026-08-29; no writers enabled yet.**
- [x] Add idempotency/grouping fields to `Notification` without breaking current clients — **additive nullable fields added 2026-08-29; legacy API shape unchanged.**
- [x] Add normalized preferences and migrate legacy values conservatively — **shadow-only `NotificationPolicy` and exact type/channel `NotificationPreference` rows added and applied in revision 059 on 2026-08-29; legacy APIs/readers and delivery behavior remain unchanged.**
- [x] Add feature flags and kill switches per external channel and intelligence — **a shared deterministic gate now covers `MOBILE_PUSH`, `EMAIL`, `WEB_PUSH`, and `INTELLIGENCE` with global kill switch, denylist, explicit/internal allowlist, and stable percentage cohort ordering. Mobile dispatch uses the shared gate; unimplemented email, web-push, and intelligence paths default off, and intelligence additionally defaults to shadow-only (2026-08-31).**
- [x] Define metrics, structured logs, and lifecycle inspection tooling — **a staff-authenticated `GET /api/v1/notifications/operations/metrics` endpoint now calculates actionable delivery groups with status-specific due/stale predicates plus 24-hour failure and persisted-interaction counts directly from PostgreSQL, avoiding process-local metric drift; inspection emits only bounded aggregate labels and structured aggregate logs. The read-only `scripts/inspect_notification_lifecycle.py` provides the same aggregate view or one redacted notification delivery/attempt/interaction/decision trace while omitting user IDs, addresses, tokens, bodies, action values, provider correlation/response data, and error details; remaining internal IDs, types, reason codes, and timestamps are documented as authorized operational data. Both SQL paths were exercised against configured PostgreSQL, including detailed physical-preview evidence; 51 notification/push tests, Ruff, formatting, and targeted mypy passed (2026-08-31).**

Clients:

- [x] Adopt the canonical action contract while retaining old `actionData` compatibility — **web and mobile resolve allowlisted canonical actions first, fall back to legacy `actionData`, and route unknown actions to the notification centre (2026-08-29).**
- [x] Add interaction idempotency ids — **web and mobile interaction mutations send stable client-generated event ids to the canonical interaction endpoint (2026-08-29).**

Exit criteria: old behavior still works; new writes produce auditable delivery plans in shadow mode; no external sending behavior changes.

### Phase 1 — canonical in-app platform

- [x] Move notification ownership out of `personal_learning` into the notification domain — **the sole ORM mapping now lives in `domains.notifications`, with a compatibility re-export preserving legacy imports (revision 060, 2026-08-29).**
- [x] Add pagination, history, unread count, mark-all-read, and interaction endpoints — **the canonical API provides opaque descending `(createdAt, id)` cursor history, status filters, an independent active-unread count, lifecycle mutations, and idempotent interactions (2026-08-29).**
- [x] Add idempotency and grouping to existing producers — **learning and progress producers now emit canonical taxonomy types and deterministic per-user keys; grouped replacement archives prior unread evidence before appending (revision 060, 2026-08-29).**
- [x] Implement WebSocket events with polling fallback — **authenticated compact invalidation hints trigger refetches, while polling plus focus/reconnect recovery remains authoritative; socket fanout is currently process-local and best-effort (2026-08-29).**
- [x] Migrate web and mobile centres to the shared contract — **both clients use canonical history, status/lifecycle actions, interactions, and the same canonical-first/legacy-compatible action semantics (2026-08-29).**
- [x] Add bell/badges and badge reconciliation — **the web shell exposes a global unread bell and browser app badge; mobile exposes the count in `TopNav` and reconciles the native badge (2026-08-29).**

Exit criteria: web and mobile show the same notification lifecycle and action semantics; reconnect/refetch recovers from missed realtime events. **Verified 2026-08-29 through web/mobile integration and HTTP poll, focus, and reconnect recovery; realtime hints do not carry authoritative state.**

### Phase 2 — mobile push made real

- [x] Implement Expo sender, tickets, receipts, dead-token handling, and retries — **canonical EXPO deliveries now retain ticket/receipt attempts, distinguish provider acceptance from delivery, retry bounded transient failures, and disable `DeviceNotRegistered` destinations; semantic re-review and 56 targeted backend tests passed (2026-08-30).**
- [x] Migrate `DeviceToken` rows into `PushInstallation` — **revision 061 was applied to the configured PostgreSQL and reconciled at head: all 5 retained Android `DeviceToken` rows are represented by 5 EXPO installations, with 0 uncovered mobile tokens, 0 WEB migrations, 0 duplicate delivery destinations, and both Phase 2 indexes present (2026-08-30).**
- [x] Fix stable installation identity, cold-start taps, pending auth actions, and route unification — **mobile now persists installation identity and revocation retries, requests permission only explicitly, and routes foreground/warm/background/cold/auth-delayed taps through the canonical resolver after server ownership proof; 42 suites/1,151 tests, typecheck, lint, and Android export passed (2026-08-30).**
- [x] Run staged physical-device delivery tests on iOS and Android — **production-like EAS preview builds targeting `https://staging-api.maigie.com` passed on physical Android and iPhone on 2026-08-31 (Android build `c9868c68-939b-467c-8269-064701d562ca`; iOS ad hoc build `a76e6256-06bd-4bdf-9934-979a6accf432`). At 0% rollout with only user `f7087cda-d671-4d0e-b7cb-da09673fa445` internally allowlisted, both platforms displayed foreground, background, and terminated-app notifications exactly once. All six platform deliveries were accepted and reconciled to `DELIVERED` in one attempt by the deployed staging workers. Background and cold-start taps reached study plan `2eb581f397434c299054e836b` and persisted exactly one platform-correct `OPENED` plus `ACTIONED` pair per tap. Foreground and cold-start routing were swift on both platforms; background routing was effectively immediate on iOS and approximately 2s on Android. A stale Android development token returned `DeviceNotRegistered` and was disabled automatically. Online Android logout immediately disabled its installation and excluded it from a control notification; re-login safely reactivated the same stable row. Offline iOS logout remained uncommitted while disconnected, then flushed its durable unauthenticated revocation after reconnect and was excluded from a control notification. Disabled rows intentionally retain tokens as evidence; `disabledAt` prevented planning.**
- [ ] Enable for internal users, then 5%, 25%, 100% — **the physical matrix used a temporary single-user staging allowlist while deterministic rollout remained `0`. Staging was redeployed after validation with the sender enabled, empty allowlists, 0% rollout, and a 900s receipt delay, so no users are eligible; post-deployment API, PostgreSQL, and Redis health checks passed. The email channel reached the same state on 2026-09-01: one real message was accepted by Resend for the internal test user through the canonical path (provider id recorded on the attempt), and the sender is enabled with empty allowlists at 0%. Do not advance either channel's cohort without an explicit operational rollout review.**

Exit criteria: provider acceptance and receipt evidence exist; invalid tokens are disabled; no preference, quiet-hour, or duplicate-send violations occur. **The EAS preview provider/display/tap matrix and physical online/offline logout revocation gates passed on Android and iOS. Production rollout remains disabled at 0%; only operational rollout approval and the staged 5%/25%/100% progression remain (2026-08-31).**

#### Cohort gate for both external channels (agreed 2026-09-01)

The same gate governs mobile push and email, because both are now planned and dispatched by
the same orchestrator and the failure modes are shared.

**Hold durations.** Internal allowlist for at least **3 days**; then 5% for at least **7
days**; then 25% for at least **7 days**; then 100%. Seven days is not padding — the weekly
summary and the digest preference only exercise themselves once per week, so a shorter hold
advances a cohort without ever having observed its slowest path. Each hold must also span at
least one quiet-hour window and one local-day boundary, which is where timezone and deferral
bugs surface.

**Stop and roll back on any of:**

- a duplicate send of the same notification to the same destination;
- any delivery to a learner whose consent for that channel is off, or inside quiet hours;
- an actionable backlog that stops draining, or an oldest-actionable age that keeps climbing;
- a rise in permanent provider failures, or any `DeviceNotRegistered`/bounce rate above the
  baseline recorded before the cohort opened;
- a rise in dismissals, unsubscribes, or push-permission revocations against the previous
  cohort.

Rollback is a configuration change — set the channel's `*_ENABLED` to `false`, or its
`ROLLOUT_PERCENT` to `0`, or add the affected users to the denylist. **Decide before opening a
cohort what rollback does to rows already planned:** ineligible deliveries are deferred, not
cancelled, so re-enabling later releases everything that accumulated in the meantime as one
burst unless those rows are expired first.

The cohort hash is stable, so raising the percentage only adds learners and never reshuffles
the ones already included. Watch it through `GET /api/v1/notifications/operations/metrics` and
`scripts/inspect_notification_lifecycle.py`.

### Phase 3 — preferences and email unification

- [x] Ship shared notification settings on web and mobile — **one contract now serves both clients: `GET`/`PUT /api/v1/notifications/settings` exposes an engagement master switch, four product categories (Learning, Progress, Social & classroom, Product updates) across in-app/mobile-push/email, quiet hours, an interruption budget capped at the platform default of 5, and the weekly digest slot. One advisory-locked transaction writes `NotificationPolicy`, category-level `NotificationPreference` rows, and — for compatibility while the legacy senders still read them — `UserPreferences.notifications`/`emailScheduleReminder`/`emailWeeklyTips`/`push*` plus `LearningProfile` quiet hours and daily cap; previously migrated exact-type overrides in a changed category are realigned so they cannot outrank the new choice. Reads fail closed: absent normalized rows fall back to legacy values, external channels default off, and only in-app Learning/Progress default on. Security and account-recovery email are reported as mandatory and no open tracking is offered. Web adds a Notifications settings tab from regenerated OpenAPI types; mobile adds a settings screen that states device push permission separately from product consent, because consent recorded here cannot deliver anything the OS has not granted. Verified against configured PostgreSQL for the internal test user: read, write, re-read, an idempotent repeat write, and the legacy dual-write all matched, then the original effective settings were restored. 53 backend tests, Ruff, format, and targeted mypy pass; web tsc/eslint/3 vitest tests and generated-type drift check pass; mobile typecheck, 241 endpoint-guard tests, 14 settings-rule tests, and the 1,249-test suite pass at an unchanged lint budget (2026-09-01).**
- [x] Route learning/progress email through the orchestrator and delivery ledger — **`create_notification` now plans an `EMAIL` delivery alongside push, and revision `063_notification_email_channel` gives that row an addressable destination: `destinationRef` holds a SHA-256 of the recipient address, and a partial unique index on `(notificationId, channel)` where `destinationId IS NULL` makes a duplicate email impossible. Planning requires `EMAIL` in the type's allowed channels, the channel kill switch, the deterministic cohort gate, engagement consent, the legacy master switch, and an enabled preference row — failing closed on every missing record. A weekly preference emails only the types that are themselves periodic summaries; for anything else it is recorded as `DIGEST_NOT_SUPPORTED` rather than silently emailing each item. `notifications.dispatch_email` claims a bounded batch every 300s, marks rows `SENDING` before any provider call, rechecks consent, quiet hours, rollout and in-app lifecycle immediately before sending, re-reads the address, and writes one `NotificationDeliveryAttempt` per request. The shared transport now returns `(provider, messageId)` and raises a classified `EmailProviderError`, so transient failures retry with jittered backoff and permanent ones fail once. Acceptance is recorded as `ACCEPTED`, never `DELIVERED`: nothing here can observe an inbox. Two latent bugs were fixed on the way — `expire_due_deliveries` would have relabelled a sent email `EXPIRED`, and both producer tasks ran without `ensure_db()`. The schedule-reminder and weekly-summary producers now create canonical notifications instead of sending directly, are keyed by block id and ISO week so re-runs replay rather than duplicate, and are scheduled in beat for the first time; their reminders now also appear in the notification centre rather than only an inbox. Verified against the configured PostgreSQL with the transport substituted: 20 checks covering planning, replay, acceptance evidence, suppression when consent is withdrawn before dispatch, weekly-versus-immediate semantics, and backlog honesty all passed, then every test row was deleted and the user's settings restored. 4,069 backend tests, Ruff, and format pass (2026-09-01).**
- [x] Preserve transactional auth/security behavior while recording delivery evidence — **revision `065_outbound_message_evidence` adds `OutboundMessage`: message class, purpose, hashed address, provider, provider message id, status, and duration. Deliberately not linked to `Notification`, because there is no notification and creating one would put a security email into a learner's notification centre; `userId` is nullable because an invite is addressed to someone who may not have an account. Behaviour is unchanged — auth and security mail still bypasses the orchestrator, consent, quiet hours and the budget, since a preference must never be able to lock someone out of their own account — but every send now leaves a record, so "the reset code never arrived" is answerable: `SKIPPED` (never attempted, no usable provider), `FAILED` (provider refused, with the classified error), or `ACCEPTED` (with the provider's own id). Evidence never breaks a send: the recorder swallows its own errors, because a reset that fails for want of an audit row is worse than a reset with no audit row. No content is stored — not the code, body, subject, or address, only its SHA-256, hashed identically to the notification ledger so an operator can join a transactional record to a suppression for the same mailbox. Password reset is classed `SECURITY` rather than `AUTH` so it can be audited and retained on its own terms. One latent bug found by sending a real email and finding no row: `OutboundMessage.userId` foreign-keys `User.id`, and importing only the notifications models raises `NoReferencedTableError` at flush — invisible in a request, fatal on the worker path where auth mail is actually sent, and silent because the recorder catches its own failures. Verified against configured PostgreSQL with a real Resend send: 11 checks including class, purpose, provider id, no plaintext address, and no stored code (2026-09-01).**
- [x] Consolidate SMTP/Resend/Brevo responsibilities — **one transport chain, and providers that cannot send are skipped instead of attempted. `SMTP_HOST` was `smtp.gmail.com` with no username or password, and this module always authenticates, so every email spent ~2.2s being refused with `530 Authentication Required`, logged an error, then succeeded via Resend. Mail arrived, so nothing looked broken; the cost was latency on every message and an error log that trained its reader to skip past it. `_smtp_usable()` now treats "cannot authenticate" as "not configured", `_email_transport_configured()` asks whether any provider *in the configured chain* could actually send — a `smtp_only` strategy with no usable SMTP is now correctly unconfigured rather than falsely ready — and the failure message names what was skipped and why. Measured effect: a real verification email went from ~2,234ms to 697ms. The misleadingly named `src/integrations/brevo/email_service.py` moved to `src/domains/identity/emails.py` and the package was deleted: it never spoke to a Brevo API, Brevo is just one possible SMTP host chosen by configuration, and the name sent readers looking for a client that was never there. Auth mail is owned by identity; `send_bulk_email` and the shared transport stay in shared infrastructure for the billing and intelligence callers that compose their own HTML. 14 new tests, 4,205 total, Ruff and format pass (2026-09-01).**
- [x] Add unsubscribe and provider webhook processing — **revision `064_email_suppression_events` adds `EmailSuppression` (address-level, hashed, one active row per address by partial unique index, released rather than deleted so history survives) and `EmailProviderEvent` (unique on `(provider, providerEventId)`, written in the same transaction as its effect). Unsubscribe is a signed HMAC token carrying user and scope with no stored state, so a link followed from an inbox needs no session; `List-Unsubscribe` and `List-Unsubscribe-Post` headers make Gmail and Yahoo offer one-click (RFC 8058), `POST /api/v1/notifications/unsubscribe` acts without a confirmation step and answers 200 even for an invalid token so it cannot be used as an oracle, and the `GET` variant tells a person what changed and where to adjust it. Unsubscribing writes through the same settings path the UI uses, so it is visible in the settings screen rather than a hidden side table, and it never touches in-app or mandatory mail. Suppression is enforced at planning and rechecked immediately before sending, because the bounce that caused it may have arrived in between. `POST /api/v1/webhooks/email/resend` verifies Svix-style signatures (implemented directly rather than adding a dependency; multiple space-separated candidates supported for secret rotation), fails closed when unconfigured, and maps events: `delivered` promotes `ACCEPTED` to `DELIVERED` — so `DELIVERED` finally means delivered — while a *permanent* bounce or a complaint fails the delivery and suppresses the address, and a soft or unlabelled bounce fails only the attempt because a full mailbox works again tomorrow and a wrong suppression is invisible and permanent. Late events cannot rewrite a terminal truth: only `ACCEPTED` may become `DELIVERED`. 27 new tests plus a suppression test on the dispatcher; verified against the configured PostgreSQL with the transport substituted — 18 checks covering promotion to `DELIVERED`, replay leaving one event row, a forged body refused, hard-bounce suppression, planning skipped while suppressed, release restoring sending, and a scoped unsubscribe leaving other categories and in-app untouched — then every row was deleted and settings restored. 4,134 backend tests, Ruff, and format pass (2026-09-01).**
- [x] Add daily/weekly digest construction — **a digest preference now means what it says. Revision `066_notification_digests` adds `NotificationDigest` (unique on `(userId, category, period, periodStart)`, `itemCount >= 1` enforced so an empty digest cannot be recorded) and `NotificationDigestItem` (globally unique on `notificationId`, so a notification belongs to at most one digest ever). Three canonical types — `learning.digest`, `progress.digest`, `social.digest` — one per settings category, because consent is expressed per category and a single cross-category digest would either exceed what the learner agreed to or withhold what they asked for. All three are `digestible=False` so a digest can never become an item in a later digest, and none allows mobile push: the point of a digest is to stop interrupting. Periods are the learner's own, via the new DST-correct `local_week_bounds` — a week containing a spring-forward is 167 hours, and the settings `digestDayOfWeek` (0 = Sunday) is converted explicitly to Python's `weekday()` (0 = Monday) rather than silently shifting every learner by a day. Only *finished* periods are summarised, so a partial week is never sent. The planner runs hourly because periods close at different instants worldwide, and the unique key makes a repeat run a no-op. Candidates exclude anything already read, dismissed, archived, or previously digested, and only types whose own taxonomy permits email — `learning.review_due` is digestible but email-forbidden, and a digest must not smuggle it into an inbox. The individual-send suppression reason changed from `DIGEST_NOT_SUPPORTED` to `HELD_FOR_DIGEST`, because it is no longer a refusal. 24 new tests; verified against configured PostgreSQL with the transport substituted — a real weekly digest was built from backdated items plus the learner's genuinely unread ones, an `EMAIL` delivery was planned with no push, a second run added nothing, and no item was claimed twice — then every row was deleted and settings restored. 4,230 backend tests, Ruff, and format pass (2026-09-01).**

Exit criteria: every non-transactional email is consented, policy-governed, and auditable; hard bounce and complaint suppression works. **Met on the engineering side (2026-09-01): every non-transactional email is planned from the consent matrix, rechecked at dispatch, and recorded as a `NotificationDelivery` with per-attempt evidence; transactional mail keeps its bypass but now records `OutboundMessage` evidence; hard bounce and complaint suppression is enforced at planning and at send. Both external channels remain at 0% rollout pending the cohort gate above, and provider webhooks must be registered with `RESEND_WEBHOOK_SECRET` before suppression can fire in a deployed environment.**

### Phase 4 — web push

- [ ] Complete PWA manifest/icons and service worker.
- [x] Add VAPID configuration and web subscription APIs — **the whole server side of web push, and the dependency decision is part of the design. `pywebpush` was tried and rejected: it dragged `cryptography` from 46 to 50, breaking the bound `fastapi-mail` declares, and added the entire `aiohttp` stack for a codebase that sends over `httpx`. Instead one leaf package, `http-ece = "1.2.1"` pinned exactly, supplies RFC 8188/8291 payload encryption — the part nobody should hand-roll, from the project that wrote the reference implementation — while the VAPID JWT is signed with the `python-jose[cryptography]` already present and delivery uses the existing `httpx`, so retry, expiry, and `Retry-After` handling stay ours rather than a library's. Pinned rather than caret-ranged because a minor bump changes ciphertext framing, and a payload a browser cannot decrypt fails invisibly inside the push service. `scripts/generate_vapid_keys.py` emits a pair once per deployment; the keys are an identity, not a rotating secret, because every subscription is bound to the public key it was created with and replacing the pair silently invalidates every subscription in the field.**

    **The endpoint is the most dangerous field in the notification system: a URL chosen by the caller that a background worker POSTs to. `web_push_endpoint.py` requires `https`, refuses embedded credentials and non-443 ports, and matches the host against `WEB_PUSH_ALLOWED_ENDPOINT_HOSTS` — the push services of Chrome, Edge, Firefox, and Safari — with suffix entries matched at a label boundary so `evilnotify.windows.com` cannot pass as `.notify.windows.com`. It runs again at send time, not only at subscribe time, because a row stored before the allowlist tightened must not keep its permission. `p256dh` must be a point that actually lies on P-256 and `auth` exactly 16 bytes, checked before storage rather than discovered on the first send, by which time the learner has been told notifications are on.**

    **The two subscription secrets are now genuinely encrypted at rest, because the columns were named `p256dhEncrypted`/`authEncrypted` and holding plaintext there would have been a lie in the schema. Endpoint plus `p256dh` plus `auth` is sending authority — push services do not authenticate senders — so a database dump should not hand over the ability to push messages to learners styled as us. AES-256-GCM under an HKDF subkey of `SECRET_KEY`, following what unsubscribe tokens already do rather than adding another environment variable to keep in sync. A rotated `SECRET_KEY` makes rows unreadable, and that is handled honestly: the dispatcher prunes the subscription so the learner is asked to resubscribe, instead of failing identically on every run forever.**

    **`upsert_mobile_installation` became `upsert_push_installation(..., transport=)`: Expo and Web Push have the identical problem — a globally unique provider address that migrates between installations, accounts, and reinstalls, where whoever holds it now must be the only row that can be pushed to — and differ only in which column carries it. The advisory-lock label stayed `push-token:` deliberately, since an Expo token and an `https` endpoint can never collide and changing it would have broken mutual exclusion during a rolling deploy. Revocation splits: mobile keeps its secret because a logout may happen with no usable session, while web uses `disable_push_installation_by_address` scoped to the caller, because a browser can only unsubscribe from a page that is already authenticated and knows nothing about itself but its endpoint.**

    **Routes: `GET /api/v1/push-installations/web/capability`, `POST /api/v1/push-installations/web`, `POST /api/v1/push-installations/web/revoke`. Subscribing is refused with 403 when web push is not available for that learner rather than stored for later, because a subscription nothing will ever send to would make the settings screen claim web push is on while it is not. Revoking always answers 204: the client calls it on logout and on permission withdrawal, both of which are intentions rather than assertions that a row exists. `dispatch_due_web_push` mirrors the email and mobile dispatchers — claim a bounded batch as `SENDING` before any provider call, recheck consent and quiet hours at send time, record one attempt row per request — and adds the step only this channel has: 404 and 410 prune the subscription rather than counting a failure, because a push service saying the browser is gone is authoritative in a way no email bounce is. `ACCEPTED` stays `ACCEPTED`; nothing here can see whether a browser was awake. Scheduled at 60s alongside mobile push, with no receipt phase, so a push is settled by the end of the batch that sent it.**

    **No migration: `PushInstallation` already carried `endpoint`, `p256dhEncrypted`, and `authEncrypted` behind a partial unique index, and `WEB_PUSH` was already in the delivery channel constraint. 85 new tests plus 5 settings tests; verified against the configured PostgreSQL with only the HTTP call substituted — 45 checks covering capability, consent written through the real settings `PUT`, secrets unreadable in the table but decrypting to the browser's own keys, one delivery planned per subscription, a real aes128gcm request whose body contains no plaintext, `ACCEPTED` with its attempt row and no queued retry, a 410 failing the delivery and pruning the subscription with its endpoint released, nothing planned once no live subscription exists, resubscribe reusing the same row, and an endpoint learned elsewhere failing to silence another learner — then every row was deleted and settings restored. 4,325 backend tests, Ruff, and format pass. `WEB_PUSH_ENABLED` stays `false`: there is no service worker yet, so an enabled sender would only log skips (2026-09-01).**
- [ ] Add browser capability/permission UX and settings state — **settings state done, UX outstanding. The matrix had no web push toggle at all, so the consent row the dispatcher requires could never exist and the channel was unreachable by construction. `NotificationCategorySetting.webPush` closes that, and `webPushAvailable` stopped being hard-coded `false`. The write is deliberately three-valued: absent means *unchanged*, not off, because this matrix is submitted by whichever client the learner happens to be using and a mobile build predating web push sends no `webPush` field — treating that as a decision would silently revoke a consent given on a laptop, a change they never made on a screen they were not looking at. Reads always return a concrete boolean. There is no legacy fallback, because no historical column could express browser consent and "not asked yet" is the only honest starting point. The browser-facing half — permission prompt, capability detection, replacing the "not available yet" copy — is web client work and remains.**
- [ ] Add push/click handlers and route allowlisting.
- [ ] Validate Chrome, Edge, Firefox, Safari macOS, and installed iOS PWA behavior where supported.

Exit criteria: web subscriptions can be created, rotated, disabled, and pruned; notification clicks resolve only allowed actions. **Half met (2026-09-01): creation, rotation, disabling, and pruning are implemented and verified against PostgreSQL. Click resolution is untouched — it needs the service worker, which is the remaining web client work, and the payload deliberately carries only the canonical action and never a URL so the worker resolves it against the client's own allowlist rather than trusting anything the server sent.**

> Note while here: `notifications.recover_stale_mobile_push` recovers stale `SENDING` rows for *every* channel, not just mobile — the underlying `recover_stale_sending` has no channel filter, so crashed web push batches are already covered. The task name is now misleading. Left alone rather than renamed, because renaming a beat entry orphans the old schedule; worth correcting in Phase 7 cleanup.

### Phase 5 — deterministic intelligence baseline

- [ ] Centralize candidate ranking, attention budgets, timing, grouping, and channel eligibility.
- [ ] Add outcome attribution rules by notification type.
- [ ] Store decision records and reason codes.
- [ ] Run recommendations in shadow mode against existing rule decisions.
- [ ] Establish control dashboards and experiment framework.

Exit criteria: deterministic policy improves or preserves meaningful-action-per-interruption without worsening fatigue guardrails.

### Phase 6 — learned ranking and bounded LLM assistance

- [ ] Train/evaluate a versioned ranker from reliable interaction/outcome data.
- [ ] Run offline evaluation, shadow mode, then guarded experiments.
- [ ] Add optional LLM digest summarization/template selection on the heavy queue.
- [ ] Validate structured output, cost limits, latency, content safety, and deterministic fallback.
- [ ] Add model/policy rollback tooling.

Exit criteria: statistically credible improvement in meaningful outcomes with no consent, fairness, fatigue, cost, or reliability regression.

### Phase 7 — migration completion and cleanup

- [ ] Remove legacy `/learning/notifications` adapters after client adoption.
- [ ] Remove obsolete preference columns after one compatibility release.
- [ ] Remove direct engagement push/email calls from feature domains.
- [ ] Retire the obsolete provider path and `pushedAt` semantics.
- [ ] Finalize data retention and operational runbooks.

## 15. Validation strategy

The implementation requires tests even though this document itself adds no runtime code.

Backend:

- taxonomy and action schema contract tests;
- property tests for quiet hours across DST and midnight boundaries;
- preference matrix and transactional exception tests;
- idempotency, dedupe, grouping, and replacement concurrency tests;
- attention budget tests across channels;
- dispatcher crash/retry/reconciliation tests;
- provider adapter tests with normalized failures and token pruning;
- webhook signature and replay tests;
- API ownership and pagination tests;
- intelligence constraint tests proving recommendations cannot violate hard policy.

Web:

- canonical action resolver and allowlist tests;
- query/mutation and optimistic rollback tests;
- unread badge and reconnect reconciliation tests;
- service-worker push/click tests;
- VAPID conversion and unsupported-browser tests;
- settings capability/permission state tests.

Mobile:

- filesystem-backed action route tests following the existing reflect route suite;
- cold-start, foreground, background, logged-out, and post-login tap tests;
- installation/token rotation tests;
- badge reconciliation tests;
- physical-device EAS smoke matrix for iOS and Android.

End to end:

- one intent produces one in-app item and only eligible channel deliveries;
- changing preferences before dispatch suppresses the send;
- quiet hours defer then release in learner-local time;
- repeated task execution does not duplicate external sends;
- opening a push marks the correct interaction and routes correctly;
- provider failure retries until expiry and remains inspectable;
- account deletion disables all destinations.

## 16. Decisions

### Decision A: one canonical notification object fans out to channels

Email, push, and in-app are not separate feature implementations. They share intent, policy, action, preference, and evidence.

### Decision B: Expo is the first mobile push transport

The current client registers Expo tokens. The backend will meet that contract instead of asking the client to switch silently to native FCM tokens.

### Decision C: web push uses standards-based VAPID subscriptions

A web push subscription is stored as structured endpoint/key material, never in `DeviceToken.token`.

### Decision D: in-app remains available when optional external channels fail

Push and engagement email are amplifiers. Their failure does not erase the user's durable notification.

### Decision E: hard policy always outranks intelligence

Consent, security classification, quiet hours, action validity, TTL, and attention caps are deterministic validators around every recommendation.

### Decision F: no live LLM call on dispatch

LLM assistance is precomputed on the heavy queue and has deterministic copy/policy fallback.

### Decision G: client routes are not stored by the backend

The backend emits a canonical action. Web and mobile own route mapping and validate that destinations exist.

### Decision H: provider acceptance is not claimed as delivery

Lifecycle statuses name what is actually known. Receipts improve evidence where available.

### Decision I: intelligence optimizes meaningful action per interruption

Raw opens and send volume are diagnostics, not the objective. Fatigue and unsubscribe measures are first-class guardrails.

### Decision J: migration is additive before destructive

Existing endpoints, records, and preferences remain compatible until deployed clients have adopted the new contract and effective user choices have been preserved.

## 17. Open product decisions before Phase 0 closes

Decisions 2, 3, and 6 were answered on 2026-09-01 and are implemented in the settings contract. Decision 1 is answered only for the mandatory floor; decision 4 is answered only for the categories the first matrix exposes. The rest remain open.

1. Which security/account events must use every reachable channel, and which remain email-only? — **Partly answered: security and account-recovery email is mandatory and cannot be switched off. Which of those events may also use other channels is still open.**
2. Which notification categories appear in the first settings matrix? — **Answered: Learning, Progress, Social & classroom, and Product updates.**
3. What should the default engagement interruption budget be, and may users increase it? — **Answered: five optional interruptions per local day, lowerable to one and not raisable past five.**
4. Which learning events are immediate versus digest by default? — **Partly answered at category level: Learning and Progress are immediate in-app, external channels start off, and the only digest offered is weekly and opt-in. Per-type defaults inside those categories are still open.**
5. How long should notification history and interaction-level analytics be retained?
6. Are email open pixels acceptable under Maigie's privacy policy, or should evaluation rely on clicks/actions only? — **Answered: no open pixels. Evaluation uses clicks and meaningful actions, and the settings response states this.**
7. Which countries/ages require additional consent handling?
8. What exact product outcomes define success for each initial intelligence-eligible notification type?
9. Should social chat notifications join this platform in the first migration or after learning/progress notifications stabilize?
10. Who owns production broadcast approval and incident shutdown authority?

## 18. Recommended first implementation slice

The first shippable slice should not be “add AI” or “add web push.” It should make one existing flow correct end to end:

1. adopt `progress.goal_at_risk` in the taxonomy;
2. create canonical notification, delivery, attempt, interaction, and installation records;
3. show it in-app on web and mobile;
4. deliver it through Expo push when consented and outside quiet hours;
5. route taps through `OPEN_GOAL` on both clients;
6. record open and meaningful goal-plan action;
7. expose its lifecycle in metrics and inspection tooling;
8. run deterministic timing/channel recommendations in shadow mode.

That slice exercises the architecture without needing web push, digest email, or learned ranking to be trustworthy first. Once it is reliable, every other notification type becomes a taxonomy and producer migration rather than another notification subsystem.

---

This document is the canonical implementation checklist. Update its phases and decision records rather than creating independent email, push, in-app, web, mobile, or intelligence plans that can drift from one another.
