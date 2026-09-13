"""Bug Hunt domain.

A paid testing programme that runs in **seasons**. Testers exercise the web, Android and iOS clients,
submit bugs and feedback against their own Maigie account, and are paid per accepted submission —
either as a Plus pass granted into their billing inventory, or as cash transferred by hand to a
Nigerian bank account.

Two routers, mounted separately in `src/app.py`:

- `router` at `/api/v1/bug-hunt` — the participant surface. Mostly authenticated; `GET /program` and
  `GET /seasons` are deliberately open, because the landing page renders the reward table from them
  and a marketing page that needs a token is a marketing page nobody reads.
- `admin_router` at `/api/v1/admin/bug-hunt` — staff triage and super-admin money, following the
  same prefix convention as `careers`, `content`, `finance` and `research`.

**The two invariants this package exists to protect** (see `docs/implementation/bug-hunt-program-plan.md`):

1. Money is an append-only ledger of signed kobo integers. No cached balance, no floats, and no
   staff-typed amount on the reward path — a triager sets category and severity, and the season's
   matrix decides what that is worth.
2. A season is a row, never a deploy. Every value that differs between Season 1 and Season 2 lives on
   `BugHuntProgram`. The wallet, by contrast, belongs to the *person* and outlives every season.
"""

from .routes import admin_router, router  # noqa: F401

__all__ = ["router", "admin_router"]
