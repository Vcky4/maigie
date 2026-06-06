"""
Management script: Circle Reimagining data migration.

Usage:
    # Dry run (report only, no mutations):
    python scripts/run_migration.py --dry-run

    # Execute migration:
    python scripts/run_migration.py

    # Execute with verbose logging:
    python scripts/run_migration.py --verbose
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

# Add the backend src to the path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Run Circle Reimagining data migration")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Produce the migration report without mutating data",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    args = parser.parse_args()

    # Configure logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    # Connect to database
    from src.core.database import db

    await db.connect()

    try:
        from src.services.migration_runner import run_migration

        print(f"\n{'=' * 60}")
        print(f"  Circle Reimagining Migration {'(DRY RUN)' if args.dry_run else ''}")
        print(f"{'=' * 60}\n")

        report = await run_migration(dry_run=args.dry_run)

        print(f"\n{'=' * 60}")
        print("  Migration Report")
        print(f"{'=' * 60}")
        print(json.dumps(report, indent=2, default=str))
        print()

        if report["status"] == "COMPLETED":
            print("✅ Migration completed successfully.")
        else:
            print(f"❌ Migration status: {report['status']}")
            sys.exit(1)

    finally:
        await db.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
