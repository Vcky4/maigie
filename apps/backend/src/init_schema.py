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
    # Import EVERY domain's db_models so all tables register with Base.metadata before create_all.
    # Done by discovery rather than a hand-maintained list: the list drifted once already — `progress`
    # was omitted, so create_all silently skipped the Goal-lifecycle tables and a fresh database came
    # up missing an entire domain. A glob cannot be forgotten when a domain is added.
    import glob
    import importlib
    import os

    domains_dir = os.path.join(os.path.dirname(__file__), "domains")
    for path in sorted(glob.glob(os.path.join(domains_dir, "*", "db_models.py"))):
        domain = os.path.basename(os.path.dirname(path))
        importlib.import_module(f"src.domains.{domain}.db_models")

    from src.shared.database.base import Base
    from src.shared.database.session import connect_db, get_session_factory

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
