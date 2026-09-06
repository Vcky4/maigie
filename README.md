# Maigie

**Maigie** is the AI-powered **academic operating system**: one workflow for courses, goals, resources, schedules, forecasts, reminders, and AI-guided chat (text + voice).

## Architecture

This is an Nx monorepo containing:

- **Backend** (`apps/backend`) - FastAPI application

### Shared Libraries

- `libs/types` - Shared TypeScript types & API client
- `libs/ui` - Shared UI components
- `libs/auth` - Shared auth helpers (token helpers)
- `libs/ai` - Shared prompts, schema for AI interactions
- `libs/db` - Prisma schema + migrations

## Getting Started

### Prerequisites

- Node.js 18+ (or 20+ recommended)
- Python 3.11+
- Poetry (for Python dependencies)

### Installation

```bash
# Install Node.js dependencies
npm install

# Install Python dependencies (for backend)
cd apps/backend
poetry install
```

### Development

```bash
# Run backend
nx serve backend
```

## Project Structure

```
Maigie/
  ├─ apps/
  │   └─ backend/                # FastAPI app (Python)
  ├─ libs/
  │   ├─ types/                  # shared TypeScript types & API client
  │   ├─ ui/                     # shared UI components
  │   ├─ auth/                   # shared auth helpers
  │   ├─ ai/                     # shared prompts, schema for AI interactions
  │   └─ db/                     # Prisma schema + migrations
  ├─ docs/
  │   └─ architecture/           # Architecture documentation
  ├─ nx.json
  ├─ package.json
  └─ README.md
```

## Deployment

See [docs/deployment/](./docs/deployment/) for deployment guides.

### Web push checklist

Every item below cost time to discover. Web push fails silently in more ways than most features:
the send is accepted, nothing appears, and no error is raised anywhere.

**Per environment, before enabling**

- [ ] Generate a VAPID pair for *that environment only*: `python apps/backend/scripts/generate_vapid_keys.py`. Never reuse one across environments — whoever holds a private key can push to every subscriber signed by it, so a staging leak would reach production learners.
- [ ] Set `WEB_PUSH_VAPID_PUBLIC_KEY` and `WEB_PUSH_VAPID_PRIVATE_KEY` as backend secrets. Production reads the `_PROD` suffixed names.
- [ ] Set the **same public key** in the web client's build secret (`VITE_WEB_PUSH_VAPID_PUBLIC_KEY_DEV` / `_PROD`). It is baked in at build time, so changing it takes effect on the next deploy, not the next page load. A key that does not match the backend produces subscriptions every push is rejected for, invisibly.
- [ ] Confirm the web app's `VITE_API_BASE_URL` points at the backend holding the matching private key. The key, the backend, and the API URL must all agree.
- [ ] Confirm `CORS_ORIGINS` includes the web origin.

**Verify before opening a cohort**

- [ ] `curl -sI https://<origin>/sw.js` returns `text/javascript` and `Cache-Control: no-cache`. If it returns HTML, the SPA catch-all is serving `index.html` and the browser will register the app shell as a service worker. `public/_redirects` must list `/sw.js`, `/site.webmanifest` and every icon explicitly.
- [ ] `/site.webmanifest` has `id`, `start_url`, `scope`, and both `any` and `maskable` icons. iOS will not install a usable PWA without them.
- [ ] `scripts/send_test_web_push.py --email <learner> --check` reports every gate open.

**Rolling out**

- [ ] `WEB_PUSH_ENABLED` is the kill switch, not the rollout. It ships `true` in `.env.example`; the cohort is what you change. Rollout stages and stop conditions are in [docs/implementation/notifications-platform-plan.md](./docs/implementation/notifications-platform-plan.md).
- [ ] Start with the tester's id in `WEB_PUSH_INTERNAL_ALLOWLIST`, which is checked before the percentage.

**Things that will waste your afternoon**

- **macOS will not show notifications if the browser lacks OS permission**, regardless of the site permission. Nothing errors; the push is accepted and never appears. Check System Settings → Notifications for that browser first.
- **Rotating `SECRET_KEY` destroys every web push subscription.** Subscription keys are encrypted at rest under a key derived from it, so after a rotation each send reads as unusable key material and prunes the subscription. Learners get no indication; they must resubscribe. This also means a subscription can only ever be sent to by the deployment that created it — rows cannot be migrated between environments.
- **A local script cannot dispatch to a deployed subscription** for the same reason. Use `--plan-only` and let that deployment's `notifications.dispatch_web_push` beat task send it.
- **The daily attention budget defers test sends** to the next local day once a learner's allowance is used. `--now` moves the schedule only; consent is still rechecked.

**Known platform differences**

- macOS Safari ignores the notification icon, showing its own and the origin instead. Chrome, Edge and the installed iOS PWA use ours.
- Apple's push service returns no `Location` header, so Safari and iOS sends have no provider message id — correlate through the delivery attempt record instead.
- macOS Safari fires `notificationclose` when a click dismisses the notification, so a click would otherwise be counted as a dismissal too. The worker suppresses it. iOS does not do this.

## Documentation

See [docs/architecture/](./docs/architecture/) for detailed architecture documentation.

See [docs/deployment/](./docs/deployment/) for deployment guides.

## License

This project is licensed under the **Business Source License 1.1 (BUSL-1.1)**.

## License Structure

### Root License
The repository is licensed under the Business Source License 1.1 (BUSL-1.1).
See [LICENSE](./LICENSE).

---

### Apache License 2.0 Licensed Directories
The following directories are licensed under the Apache License 2.0,
notwithstanding the root BUSL-1.1 license:

- `apps/backend/src/utils/`
- `apps/backend/src/schemas/`
- `apps/backend/tests/`
- `libs/types/`
- `docs/`

Each directory contains or is covered by
[LICENSE-APACHE-2.0.md](./LICENSE-APACHE-2.0.md).

---

### BUSL-1.1 Licensed Directories (Subject to Change Date)

The following directories are licensed under BUSL-1.1
and will convert to Apache License 2.0 on the Change Date:

- `apps/backend/src/routes/`
- `apps/backend/src/services/`
- `apps/backend/src/models/`
- `apps/backend/src/core/`
- `apps/backend/src/config.py`
- `apps/backend/src/main.py`
- `apps/backend/src/dependencies.py`
- `apps/backend/src/middleware.py`
- `apps/backend/src/exceptions.py`

---

### Change Date

On **2029-12-28**, all BUSL-1.1 licensed code in this repository
will automatically convert to the Apache License 2.0,
unless explicitly relicensed or moved to a proprietary repository
prior to that date.
