# DEPRECATED — Do Not Add New Routes Here

This directory contains the **legacy route handlers** from the pre-refactor architecture.

## What happened?

All routes have been migrated to domain-scoped packages in `src/domains/`.
The new route structure is:

```
src/domains/identity/routes.py          → /api/v1/auth, /api/v1/users
src/domains/billing/routes.py           → /api/v1/billing
src/domains/knowledge/routes.py         → /api/v1/knowledge
src/domains/personal_learning/routes.py → /api/v1/learning
src/domains/learning_spaces/routes.py   → /api/v1/spaces
src/domains/classrooms/routes.py        → /api/v1/classrooms
src/domains/intelligence/routes.py      → /api/v1/intelligence
src/domains/progress/routes.py          → /api/v1/progress
src/domains/admin/routes.py             → /api/v1/admin
```

## Why are these files still here?

Some domain services still **delegate** to the existing service implementations
in `src/services/` which import route helpers from here. As services are
fully rewritten within their domains, these files will be deleted.

## Rules

1. **DO NOT add new routes here.** All new work goes in `src/domains/`.
2. **DO NOT import from here in new domain code** unless wrapping for delegation.
3. These files will be deleted once all services are self-contained within domains.
