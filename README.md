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

GNU Affero General Public License v3.0 (AGPL-3.0)

See [LICENSE](./LICENSE) for the full license text.
