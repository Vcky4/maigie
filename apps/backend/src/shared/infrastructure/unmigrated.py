"""Markers for code that outlived the datastore it was written against.

The backend moved from Prisma to SQLAlchemy, but two billing modules still speak the
Prisma client API (``db_client.user.find_unique(where=...)``) and read Prisma's
camelCase attribute names off the SQLAlchemy models. They were left importing a
``db`` global from a module that no longer exists, so they failed at import; that
hid the deeper problem, which is that their bodies cannot work at all.

Rather than let those paths die on ``NameError: name 'db' is not defined`` several
frames from the cause, they bind ``db`` to the sentinel below. Any use produces a
message that names the module, the missing migration, and the fact that no write
occurred, which is the information an on-call reader actually needs.
"""

from typing import Any, NoReturn


class UnmigratedDatastoreError(RuntimeError):
    """Raised when Prisma-era code is reached after the move to SQLAlchemy."""


class PrismaClientRemoved:
    """Stands in for the deleted Prisma client and refuses every use.

    Attribute access is the first thing Prisma-era code does (``db.user``), so
    failing there gives the clearest possible stack.
    """

    __slots__ = ("_owner",)

    def __init__(self, owner: str) -> None:
        self._owner = owner

    def _fail(self, detail: str) -> NoReturn:
        raise UnmigratedDatastoreError(
            f"{self._owner} still uses the Prisma client API ({detail}), but Prisma was "
            "removed when the backend moved to SQLAlchemy. This code path has not been "
            "migrated and no data was read or written. Port it to a SQLAlchemy session "
            "via src.shared.database.get_session_factory before enabling this feature."
        )

    def __getattr__(self, name: str) -> NoReturn:
        self._fail(f"attribute {name!r}")

    def __call__(self, *args: Any, **kwargs: Any) -> NoReturn:
        self._fail("call")

    def __bool__(self) -> bool:
        # Truthy so `if db_client is None: db_client = db` style guards do not silently
        # skip the assignment and mask the failure.
        return True

    def __repr__(self) -> str:
        return f"<PrismaClientRemoved owner={self._owner!r}>"


class UnmigratedSubsystemError(NotImplementedError):
    """Raised when code depends on a subsystem that has not been migrated."""


def raise_unmigrated(subsystem: str, origin: str, consequence: str) -> NoReturn:
    """Fail with a message that says what is missing and what it costs.

    Preferable to returning ``None``, ``{}`` or ``""`` from a stub. Those values travel:
    a ``None`` router becomes ``AttributeError: 'NoneType' object has no attribute
    'route_request'`` several frames away, and an empty string looks like a model that
    answered with nothing.

    Args:
        subsystem: What is missing, in the reader's terms.
        origin: Where the implementation can be recovered from.
        consequence: What does not work until it is restored.
    """
    raise UnmigratedSubsystemError(
        f"{subsystem} has not been migrated. {consequence} "
        f"The implementation is recoverable from {origin}."
    )
