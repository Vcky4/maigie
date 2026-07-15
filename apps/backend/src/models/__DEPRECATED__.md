# DEPRECATED — Use Domain Models Instead

All Pydantic schemas have been migrated to `src/domains/*/models.py`.

These files remain only because some legacy services import from them.
They will be deleted as services are rewritten within their domains.

**New model locations:**
- `src/domains/identity/models.py`
- `src/domains/billing/models.py`
- `src/domains/knowledge/models.py`
- `src/domains/personal_learning/models.py`
- `src/domains/learning_spaces/models.py`
- `src/domains/classrooms/models.py`
- `src/domains/intelligence/models.py`
- `src/domains/progress/models.py`
- `src/domains/admin/models.py`
