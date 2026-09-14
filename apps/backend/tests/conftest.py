"""
Shared pytest configuration.

Uses the new SQLAlchemy async engine (``src.shared.database``) for
database-touching tests. Set ``SKIP_DB_FIXTURE=1`` for pure unit tests
that do not need a live database.

Tests that reference legacy modules under ``src.services`` or
``src.routes`` (removed in the domain refactor) are collected but
skipped automatically — see ``pytest_collection_modifyitems``.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

# Path segments in imports/source that indicate a test targets a
# module that no longer exists in the domain-driven architecture.
_LEGACY_IMPORT_MARKERS = (
    "src.services.",
    "src.routes.",
    "src.core.database",
    "src.schemas.subscription",
)


def pytest_ignore_collect(collection_path, config):
    """
    Skip legacy test files that import removed modules.

    Reading these files' source is fast and avoids ``ImportError``
    during collection. When you migrate a legacy service, drop the
    import-line marker and the file is picked up again automatically.
    """
    if collection_path.suffix != ".py":
        return None
    if not collection_path.name.startswith("test_"):
        return None
    try:
        source = collection_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None
    if any(marker in source for marker in _LEGACY_IMPORT_MARKERS):
        return True  # ignore this file
    return None


# ---------------------------------------------------------------------------
# Database lifecycle
# ---------------------------------------------------------------------------


#: Fixtures whose presence means a test needs a live database.
_DB_FIXTURES = frozenset({"db", "client", "auth_headers"})


@pytest.fixture
def db():
    """Declare that a test needs a live database.

    A signal, not the setup — ``db_lifecycle`` below does the connecting. Request this
    when a test talks to the database without going through ``client``.
    """
    return None


#: Hosts a destructive test suite may point at. Everything else is refused.
_LOCAL_DB_HOSTS = frozenset(
    {"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal", "db", "postgres"}
)


def _refuse_non_local_database(database_url: str) -> None:
    """Fail loudly rather than let a destructive suite run against a remote database.

    **This is a hard error, not a skip.** A skip is what you want when an environment simply cannot run a
    test; here the environment *can*, and doing so would be a catastrophe. Silently skipping would also
    hide the mistake, and a green run against no tests looks exactly like a green run against all of them.

    The near-miss that prompted it, 2026-09-14: `.env` was repointed at **production** to diagnose a
    prod-only enum failure, while `DATABASE_URL` is read from that same file. The Bug Hunt suites open
    with ``DELETE FROM "BugHuntLedgerEntry"``, ``DELETE FROM "BugHuntWithdrawal"`` and a ``DELETE FROM
    "User"`` filtered only by an email pattern, in an autouse fixture that runs before *every* test in the
    file. One ``RUN_DB_TESTS=1 pytest`` would have deleted live financial rows. Nothing in the suite
    prevented it; the only protection was remembering, and the whole point of a guard is to not have to.

    An escape hatch exists because a legitimate remote scratch database is a real thing, but it is
    deliberately awkward to type and names what it is doing.
    """
    from urllib.parse import urlparse

    if os.getenv("ALLOW_DESTRUCTIVE_TESTS_ON_REMOTE_DB", "").lower() in ("1", "true", "yes"):
        return

    # Parse rather than substring-match: `postgresql://user@prod-host/localhost_lookalike` contains the
    # string "localhost" and is not local.
    host = (urlparse(database_url).hostname or "").lower()
    if host in _LOCAL_DB_HOSTS:
        return

    pytest.fail(
        "Refusing to run database tests against a non-local host.\n\n"
        f"  DATABASE_URL host: {host or '(unparseable)'}\n\n"
        "These suites truncate tables and delete User rows. Point DATABASE_URL at a local scratch\n"
        "database, for example:\n\n"
        '  RUN_DB_TESTS=1 DATABASE_URL="postgresql://$(whoami)@localhost:5432/scratch" pytest ...\n\n'
        "If the target really is a disposable remote database, set\n"
        "ALLOW_DESTRUCTIVE_TESTS_ON_REMOTE_DB=1 and be certain.",
        pytrace=False,
    )


@pytest.fixture(scope="function", autouse=True)
def no_outbound_email(request, monkeypatch):
    """No test sends real email. Ever.

    ``.env`` can reach ``os.environ`` as a side effect of collection, which is how a populated
    ``RESEND_API_KEY`` ends up configured during a test run. Any domain that emails as part of a
    committed action then makes a live HTTP call to a third party from the test suite — observed after
    Bug Hunt triage started sending outcome email: `pytest tests/test_bug_hunt_reports.py` posted a
    dozen messages to Resend, which refused them with a 422 only because the recipients were
    ``@example.com``. With plausible addresses in a fixture it would have emailed real people.

    Clearing both providers makes ``_email_transport_configured()`` false, so every sender takes its
    documented "skip quietly" path. That is the correct default for a test: the sending is somebody
    else's tested behaviour, and what a domain test cares about is that it did not raise.

    Tests that *are* about email opt out by requesting the ``transport`` fixture, which sets the
    credentials it needs. Ordering works because this runs first and ``transport`` overwrites it.
    """
    if "transport" in request.fixturenames:
        return
    from src.config import settings

    monkeypatch.setattr(settings, "SMTP_HOST", "", raising=False)
    monkeypatch.setattr(settings, "RESEND_API_KEY", "", raising=False)


@pytest.fixture(scope="function", autouse=True)
async def db_lifecycle(request):
    """Connect/disconnect the SQLAlchemy async engine, for tests that need one.

    Which tests those are is decided **per test**, from the fixtures it requests. It
    used to be decided by a ``SKIP_DB_FIXTURE`` environment variable, which is
    process-wide while pytest imports every module during collection — so one unit-test
    module setting it at import time disabled the database for the *entire* run. Over
    forty modules set it. The effect was that every database-backed test skipped
    regardless of ``DATABASE_URL``, and in a summary line a skipped test looks much
    like a passing one. It hid the flashcard API suite for a whole stage.

    Reading ``request.fixturenames`` fixes that without touching those forty modules,
    and without the opposite failure: a test that never asked for a database is left
    alone rather than being connected — or skipped — on another file's behalf. The
    ``SKIP_DB_FIXTURE`` lines are now inert.

    **Running them is opt-in.** ``DATABASE_URL`` is not a safe trigger: the configured
    database is a shared hosted one, and `.env` can reach ``os.environ`` as a side
    effect of collection — ``test_generation_latency`` used to do exactly that by
    exec'ing a script that calls ``load_dotenv()``. Keying off it meant ``pytest tests``
    silently spent half an hour writing to a real database, depending on which files
    were collected. ``RUN_DB_TESTS=1`` says so deliberately::

        RUN_DB_TESTS=1 DATABASE_URL=... pytest tests/test_study_plan_api.py
    """
    if not _DB_FIXTURES & set(request.fixturenames):
        yield
        return

    if os.getenv("RUN_DB_TESTS", "").lower() not in ("1", "true", "yes"):
        pytest.skip("RUN_DB_TESTS not set — skipping database-dependent test")
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        pytest.skip("DATABASE_URL not set — skipping database-dependent test")
    _refuse_non_local_database(database_url)

    from src.shared.database.session import connect_db, disconnect_db

    connected = False
    try:
        await connect_db()
        connected = True
        yield
    except Exception as e:  # pragma: no cover - environmental
        pytest.skip(f"Database connection failed: {e}")
    finally:
        if connected:
            try:
                await disconnect_db()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client against the FastAPI app.

    The app's lifespan is intentionally bypassed — ``db_lifecycle`` owns database setup
    and teardown, and recognises this fixture by name as needing one.
    """
    from src.app import app

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@pytest.fixture
async def auth_headers(client: AsyncClient):
    """Create a user, activate them, and log them in.

    Returns ``{"Authorization": "Bearer <token>"}``.
    """
    email = f"test_{uuid.uuid4()}@example.com"
    password = "StrongPassword123!"

    # The route is `/signup`. This previously posted to `/auth/register`, which
    # does not exist, so it 404'd and the skip below silently disabled every
    # database-backed test in the suite rather than reporting a broken fixture.
    signup = await client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": password, "name": "Test User"},
    )
    if signup.status_code not in (200, 201):
        pytest.skip(
            f"Signup failed (DB likely unavailable): {signup.status_code} - {signup.text[:200]}"
        )

    # Force-activate to bypass OTP verification.
    from sqlalchemy import update as sa_update

    from src.domains.identity.db_models import User
    from src.shared.database.session import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(sa_update(User).where(User.email == email).values(is_active=True))
        await session.commit()

    login = await client.post(
        "/api/v1/auth/login/json",
        json={"email": email, "password": password},
    )
    if login.status_code != 200:
        pytest.fail(f"Login failed: {login.status_code} - {login.text}")

    token = login.json().get("access_token") or login.json().get("accessToken")
    if not token:
        pytest.fail(f"No access_token in login response: {login.json()}")

    return {"Authorization": f"Bearer {token}"}
