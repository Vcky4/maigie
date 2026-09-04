# Notifications platform — operational runbook

Operational reference for the Maigie notification platform: the canonical in-app store, its email
and push channels, the intelligence layer, digests, and data retention. It covers the controls that
turn behaviour on and off, the periodic tasks and how to reason about them, and the failure modes
worth knowing before they page you. Design rationale lives in
[`../implementation/notifications-platform-plan.md`](../implementation/notifications-platform-plan.md);
this document is the "what do I do now" companion.

> Scope: backend at `apps/backend`. All configuration below is environment settings on the backend
> (`src/config.py`). Nothing here is a per-request toggle; changes take effect on the next task run or
> the next process that reads settings.

## Feature flags and kill switches

Every channel and capability is a fail-closed rollout gate: `*_ENABLED` (kill switch), `*_DENYLIST`,
`*_ALLOWLIST`, `*_INTERNAL_ALLOWLIST`, and `*_ROLLOUT_PERCENT` (stable per-user cohort). A learner is
in a capability only if it is enabled, they are not denied, and they are allowlisted or fall inside
the rollout percentage. To disable anything immediately, set its `*_ENABLED` to `False`.

| Capability | Prefix | Notes |
| --- | --- | --- |
| Email | `NOTIFICATION_EMAIL` | Requires a working provider (Resend/SMTP) and `RESEND_WEBHOOK_SECRET` for suppression. |
| Mobile push | `MOBILE_PUSH` | Expo transport. |
| Web push | `WEB_PUSH` | Needs a VAPID pair; see key rotation below. |
| Intelligence (decisions) | `NOTIFICATION_INTELLIGENCE` | Also `NOTIFICATION_INTELLIGENCE_SHADOW_ONLY` (default `True`). |
| Digest LLM copy | `NOTIFICATION_DIGEST_LLM` | Also `NOTIFICATION_DIGEST_LLM_SHADOW_ONLY` (default `True`). |
| Data retention | `NOTIFICATION_RETENTION_ENABLED` | Off by default; see retention below. |

Shadow flags matter: with `*_SHADOW_ONLY=True`, the layer runs and records but the learner still gets
the deterministic result. Turning a shadow flag off is what makes a proposal reach a learner — treat
it as the real launch step.

## Periodic tasks

All notification tasks run on the `default` queue except the per-digest LLM build, which runs on
`heavy`. Schedules are declared in `src/workers/notification_tasks.py::get_beat_schedule`.

| Task | Cadence | Does |
| --- | --- | --- |
| `notifications.plan_digests` | hourly | Builds due digests. Enqueues `notifications.process_digest` (heavy) for LLM-cohort learners; builds the rest inline. |
| `notifications.process_digest` | on demand (heavy) | Builds one learner's digest, resolving LLM copy off the planner's path. |
| `notifications.dispatch_email` | 5 min | Sends due EMAIL deliveries. |
| `notifications.dispatch_mobile_push` | 60 s | Sends due Expo deliveries. |
| `notifications.dispatch_web_push` | 60 s | Sends due Web Push deliveries. |
| `notifications.reconcile_expo_receipts` | 5 min | Resolves Expo receipts, prunes dead tokens. |
| `notifications.recover_stale_mobile_push` | 5 min | Recovers deliveries stuck in `SENDING`. |
| `notifications.schedule_reminders` | 15 min | Study-session reminder producer. |
| `notifications.review_due_reminders` | hourly | Review-due producer (Plus-gated). |
| `notifications.weekly_summary` | hourly | Weekly summary producer (ISO-week idempotent). |
| `notifications.prune_retention` | daily 03:30 | Deletes evidence past retention windows; a no-op unless enabled. |

Producers and dispatchers are idempotent (idempotency keys, `eligible_at`-gated claims), so a missed
run is caught by the next one and a double run does not duplicate sends.

## Common operations

### A channel is misbehaving — stop it now
Set the channel's `*_ENABLED=False`. In-flight deliveries already claimed will finish their current
attempt; nothing new is claimed. To stop only for some learners, use `*_DENYLIST`.

### Emails are not sending
1. Confirm `NOTIFICATION_EMAIL_ENABLED=True` and the learner is in the cohort.
2. Check the provider chain is configured (a `smtp_only` strategy with no usable SMTP is treated as
   unconfigured by design).
3. Deliveries sit in `PLANNED`/`QUEUED` with `eligible_at<=now`. If they are deferred, check quiet
   hours and the per-learner interruption budget — email honours both and sets its own release time.
4. Suppression: a hard bounce or complaint suppresses the address. Check `EmailSuppression`.

### Deliveries stuck in `SENDING`
`recover_stale_mobile_push` recovers stale mobile rows on a 5-minute cadence. A row stuck longer than
that cadence suggests a worker died mid-send; the attempt counter was already incremented, so it will
not retry forever. Investigate the worker, not the row.

### Provider webhook replay / suppression not firing
Email suppression depends on Resend webhooks. Confirm `RESEND_WEBHOOK_SECRET` is set (the endpoint
fails closed when it is not) and that the webhook is registered with the provider. Events are unique
on `(provider, providerEventId)`, so replays are safe and a re-delivered webhook is a no-op.

### Web push stopped working for everyone after a deploy
Web Push subscriptions are bound to the VAPID public key they were created with. **Rotating the VAPID
pair (or anything that regenerates it) silently invalidates every existing subscription** — browsers
reject the mismatch and the only repair is for each learner to resubscribe. Rotate only with a
resubscribe plan. Confirm `WEB_PUSH_VAPID_PUBLIC_KEY`/`_PRIVATE_KEY`/`_SUBJECT` match what was in the
field.

### Intelligence dashboard
`GET /api/v1/notifications/operations/intelligence` (staff-only) reports decisions by policy version,
mode, and reason code, the fallback count, shadow divergences, and the per-type outcome funnel. Use it
to watch a shadow layer before turning its shadow flag off.

## Data retention

`notifications.prune_retention` (daily 03:30) deletes notification **evidence** past its window. It is
**off by default** and must be enabled deliberately, because deletion is irreversible and the windows
are a policy decision.

What it prunes (never the learner-facing `Notification` row, never an in-flight delivery):

| Table | Window setting | Default |
| --- | --- | --- |
| `NotificationDelivery` (terminal only) + attempts (cascade) | `NOTIFICATION_RETENTION_DELIVERY_DAYS` | 90 |
| `NotificationInteraction` | `NOTIFICATION_RETENTION_INTERACTION_DAYS` | 365 |
| `NotificationDecision` | `NOTIFICATION_RETENTION_DECISION_DAYS` | 365 |
| `NotificationDigest` + items (cascade) | `NOTIFICATION_RETENTION_DIGEST_DAYS` | 180 |
| `EmailProviderEvent` | `NOTIFICATION_RETENTION_EMAIL_EVENT_DAYS` | 90 |

Deletes run in batches of `NOTIFICATION_RETENTION_BATCH` (default 2000) so locks stay short. Only
terminal delivery states (`ACCEPTED/DELIVERED/FAILED/EXPIRED/CANCELLED/SUPPRESSED`) are prunable;
`PLANNED/QUEUED/SENDING` are preserved at any age. Deleting a decision nulls the notification's audit
pointer (`ON DELETE SET NULL`); the notification itself is untouched.

To enable: agree the windows with the applicable retention/compliance policy, set them explicitly,
then set `NOTIFICATION_RETENTION_ENABLED=True`. The first real sweep on an old database may run for
several daily passes as it drains the backlog in batches; that is expected and safe to interrupt.

## Known gaps

- Aggregate, de-identified long-term analytics retention is not yet built; the sweep prunes raw rows
  only.
- The legacy `learning.notification_delivery` sweep and its FCM sender remain scheduled but are a
  confirmed no-op (no producer writes the legacy `PENDING`/`QUEUED` rows it drained, and `pushedAt` is
  never populated). Retiring them is a tracked follow-up, not a live path.
