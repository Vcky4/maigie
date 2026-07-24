"""
Database schema initialization script.

Creates all tables from SQLAlchemy models if they don't exist.
Safe to run on existing databases — create_all is idempotent (skips existing tables).
Run this BEFORE alembic migrations to ensure base tables exist.
"""

import asyncio
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, ".")


async def init():
    from src.shared.database.session import connect_db, get_session_factory
    from src.shared.database.base import Base

    # Import all domain models so they register with Base.metadata
    import src.domains.identity.db_models  # noqa: F401
    import src.domains.knowledge.db_models  # noqa: F401
    import src.domains.intelligence.db_models  # noqa: F401
    import src.domains.learning_spaces.db_models  # noqa: F401
    import src.domains.personal_learning.db_models  # noqa: F401
    import src.domains.billing.db_models  # noqa: F401

    await connect_db()
    factory = get_session_factory()
    engine = factory.kw["bind"]

    # Check if tables already exist (if User table exists, schema is already set up)
    from sqlalchemy import text

    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT EXISTS (SELECT FROM pg_tables WHERE tablename = 'User')")
        )
        exists = result.scalar()

    if exists:
        print("✓ Schema already exists (skipping create_all)")
        return

    # Fresh DB — create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    print("✓ Schema initialized (create_all complete)")

    # Stamp alembic to head so migrations don't try to recreate tables
    import subprocess

    subprocess.run(["alembic", "stamp", "head"], check=False)
    print("✓ Alembic stamped to head")


if __name__ == "__main__":
    asyncio.run(init())
