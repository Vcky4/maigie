"""Export the FastAPI OpenAPI schema deterministically.

The schema is the canonical API contract artifact owned by this repository.
Client repositories consume the exported JSON to generate their own
TypeScript types; no Node tooling is required here.

The FastAPI application is only constructed, never served: the lifespan
never runs, so no database or cache connection is opened.

Usage:
    poetry run python scripts/export_openapi.py
    poetry run python scripts/export_openapi.py --output openapi.json
    poetry run python scripts/export_openapi.py --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = BACKEND_ROOT / "openapi.json"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def build_schema() -> dict:
    """Build the OpenAPI schema without starting the server."""
    from src.app import create_app

    app = create_app()
    return app.openapi()


def render_schema(schema: dict) -> str:
    """Render the schema as stable, diff-friendly JSON."""
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export the OpenAPI schema.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output path for the schema (default: {DEFAULT_OUTPUT}).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if the file on disk differs from the generated schema.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rendered = render_schema(build_schema())

    if args.check:
        if not args.output.exists():
            print(
                f"OpenAPI schema missing: {args.output}\n"
                "Run: poetry run python scripts/export_openapi.py",
                file=sys.stderr,
            )
            return 1
        if args.output.read_text(encoding="utf-8") != rendered:
            print(
                f"OpenAPI schema out of date: {args.output}\n"
                "The API contract changed. Regenerate and commit the schema:\n"
                "  poetry run python scripts/export_openapi.py",
                file=sys.stderr,
            )
            return 1
        print(f"OpenAPI schema up to date: {args.output}")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"OpenAPI schema written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
