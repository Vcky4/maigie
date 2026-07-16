"""List all PostgreSQL enum types in the database."""
import asyncio
from sqlalchemy import text
from src.shared.database.session import connect_db, get_session_factory


async def main():
    await connect_db()
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(text("""
            SELECT t.typname, e.enumlabel
            FROM pg_type t
            JOIN pg_enum e ON t.oid = e.enumtypid
            ORDER BY t.typname, e.enumsortorder
        """))
        rows = result.fetchall()
        enums = {}
        for typname, label in rows:
            enums.setdefault(typname, []).append(label)
        for name, labels in enums.items():
            print(f"{name}: {labels}")


asyncio.run(main())
