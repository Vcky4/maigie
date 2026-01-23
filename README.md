# Maigie

AI-powered student companion that helps learners manage courses, set goals, discover resources, schedule study sessions, get forecasts and reminders, and converse with an intelligent assistant (text + voice).

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
