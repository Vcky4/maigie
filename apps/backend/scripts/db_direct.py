"""Resolve a DDL-safe database URL, and run a command against it.

Three ways to reach a Supabase database, and only two of them can run a migration:

- ``db.<ref>.supabase.co:5432`` — direct. Best for DDL. Publishes **only an AAAA
  record**, so it needs working IPv6; historically unreachable from this machine,
  which is why the pooler notes exist in the Prepare plan.
- ``...pooler.supabase.com:5432`` — session mode. Behaves like a direct
  connection and supports DDL, but the tenant allowance is small (15), and a dev
  server configured with ``pool_size=20, max_overflow=10`` can consume all of it.
- ``...pooler.supabase.com:6543`` — transaction mode. **Not for migrations**:
  unreliable for DDL and for prepared statements.

This picks the direct host when it is reachable and falls back to session mode,
so a migration does not depend on remembering which one works today.

    poetry run python scripts/db_direct.py alembic upgrade head
    poetry run python scripts/db_direct.py python scripts/check_prep_016.py

The resolved URL is placed in the child's environment and is never printed.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys

from dotenv import load_dotenv

load_dotenv()

_POOLER_USER = re.compile(r"^postgres\.([a-z0-9]+)$")


def _reachable(host: str, port: int, *, timeout: float = 8.0) -> bool:
    try:
        for family, socktype, proto, _, address in socket.getaddrinfo(
            host, port, 0, socket.SOCK_STREAM
        ):
            with socket.socket(family, socktype, proto) as sock:
                sock.settimeout(timeout)
                sock.connect(address)
                return True
    except OSError:
        return False
    return False


def resolve() -> tuple[str, str]:
    """Return ``(url, description)``. The description is safe to log."""
    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        raise SystemExit("DATABASE_URL is not set")

    match = re.match(r"^(postgres(?:ql)?)://([^:]+):([^@]+)@([^/:]+):?(\d+)?/(.+)$", raw)
    if not match:
        raise SystemExit("DATABASE_URL is not in a recognised form")
    _, user, password, host, _, database = match.groups()
    database = database.split("?")[0]

    ref_match = _POOLER_USER.match(user)
    if ref_match and "pooler" in host:
        ref = ref_match.group(1)
        direct_host = f"db.{ref}.supabase.co"
        if _reachable(direct_host, 5432):
            # Direct connections use the bare `postgres` user, not `postgres.<ref>`.
            return (
                f"postgresql+asyncpg://postgres:{password}@{direct_host}:5432/{database}",
                f"direct host {direct_host}:5432",
            )
        session_url = f"postgresql+asyncpg://{user}:{password}@{host}:5432/{database}"
        return session_url, f"session-mode pooler {host}:5432"

    url = raw.replace("postgres://", "postgresql://", 1).replace(
        "postgresql://", "postgresql+asyncpg://", 1
    )
    url = url.replace(":6543/", ":5432/")
    return url, f"{host}:5432"


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("usage: db_direct.py <command> [args...]")

    url, description = resolve()
    print(f"connecting via {description}", flush=True)

    env = dict(os.environ)
    # asyncpg does not accept the pgbouncer flag, and alembic re-derives the
    # driver prefix itself, so hand it the plain scheme.
    env["DATABASE_URL"] = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    for param in ("?pgbouncer=true", "&pgbouncer=true"):
        env["DATABASE_URL"] = env["DATABASE_URL"].replace(param, "")

    return subprocess.call(sys.argv[1:], env=env)


if __name__ == "__main__":
    raise SystemExit(main())
