# Architecture Documentation

This directory contains the architecture documentation for Maigie, organized by concern.

## Documentation Structure

- **[Overview](./overview.md)** - System overview, high-level requirements, and non-functional considerations
- **[Monorepo](./monorepo.md)** - Nx monorepo structure and organization
- **[Backend](./backend.md)** - FastAPI backend structure, API endpoints, and design principles
- **[Database](./database.md)** - Data models, ERD, and Prisma schema
- **[AI](./ai.md)** - AI & vector search design, intent mapping, LLM prompts, decision engine, and event architecture
- **[Frontend](./frontend.md)** - Dashboard and frontend organization
- **[Mobile](./mobile.md)** - Offline-first support and sync strategy
- **[Realtime](./realtime.md)** - WebSocket and voice communication
- **[Infrastructure](./infrastructure.md)** - Deployment, observability, security, and background jobs
- **[Flows](./flows.md)** - Example user flows and system interactions

## Quick Navigation

### For Backend Developers
- Start with [Backend](./backend.md) for API structure
- Review [Database](./database.md) for data models
- Check [AI](./ai.md) for AI integration details
- See [Infrastructure](./infrastructure.md) for deployment

### For Frontend Developers
- Start with [Frontend](./frontend.md) for UI organization
- Review [Backend](./backend.md) for API endpoints
- Check [Realtime](./realtime.md) for WebSocket integration

### For Mobile Developers
- Start with [Mobile](./mobile.md) for offline-first strategy
- Review [Frontend](./frontend.md) for shared UI components
- Check [Backend](./backend.md) for API endpoints

### For AI/ML Engineers
- Start with [AI](./ai.md) for complete AI architecture
- Review [Database](./database.md) for embedding storage
- Check [Flows](./flows.md) for AI interaction examples

