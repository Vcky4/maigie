# Monorepo (Nx) Layout — Practical Details

```
Maigie/
  ├─ apps/
  │   └─ backend/                # FastAPI app (Python)
  ├─ libs/
  │   ├─ types/                  # shared TypeScript types & API client
  │   ├─ ui/                     # shared UI components
  │   ├─ auth/                   # shared auth helpers (token helpers)
  │   ├─ ai/                     # shared prompts, schema for AI interactions
  │   └─ db/                     # Prisma schema + migrations (or SQLModel models)
  ├─ tools/
  ├─ nx.json
  ├─ package.json
  ├─ pyproject.toml
  └─ README.md
```

## Notes

* Keep a small, well-documented `types` lib for shared DTOs.
* Use Nx tasks to run lint/test/build for affected projects.

