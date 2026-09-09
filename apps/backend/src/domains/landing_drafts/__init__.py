"""Landing drafts domain.

The anonymous half of the marketing site's conversion flow: a visitor builds a starter setup on
maigie.com without an account, and the account they create afterwards claims it.

The claim itself lives in `personal_learning.routes` alongside the rest of onboarding, because that
is the domain that owns a learner's profile. This package owns the draft and nothing else.
"""

from .routes import router  # noqa: F401

__all__ = ["router"]
