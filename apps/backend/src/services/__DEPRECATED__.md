# DEPRECATED — Implementation Layer (Being Consumed by Domains)

This directory contains the **legacy service implementations** from the pre-refactor architecture.

## What happened?

All business logic is being migrated to domain-scoped services in `src/domains/*/services/`.
During the transition, domain services **delegate** to these implementations.

## Why are these files still here?

Each domain service wraps calls to the original implementation:

```python
# domains/knowledge/services/course_service.py
async def delete_course(*, course_id: str, user_id: str) -> None:
    from src.services.course_delete_service import delete_course_cascade
    await delete_course_cascade(db, course_id, user_id)
```

As each service is fully rewritten within its domain, the corresponding
file here will be deleted.

## Rules

1. **DO NOT add new services here.** All new work goes in `src/domains/*/services/`.
2. **DO NOT add new features to existing services.** Extend the domain version instead.
3. These files will be deleted incrementally as domains become self-contained.
