"""One layout for every email Maigie sends, and no templates without senders.

**The defect this prevents is a second layout, not a bad one.** Two shells existed here for
months: auth mail extended `base.html` — tables for structure, every colour stated so a client
forcing dark mode inverts the shell instead of leaving dark text on a dark card, an inbox
preview line — while billing, spaces, limit and notification mail were standalone documents
with their own body styling and no preheader. Both rendered. Nothing failed. The brand simply
drifted, and every fix applied to one shell was invisible in the other, which is why the
notification email shipped on the older one by being copied from its neighbour.

The second rule is about dead templates. Four files here had no sender at all, one of them
named in a docstring that claimed it was in use. A template nobody renders is worse than no
template: it looks like the supported way to do the thing, so the next person extends it.
"""

from __future__ import annotations

import os

os.environ.setdefault("SKIP_DB_FIXTURE", "1")

import re  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from src.shared.infrastructure.email import APP_NAME, TEMPLATE_FOLDER, _render  # noqa: E402

FOLDER = Path(str(TEMPLATE_FOLDER))

#: Every template with a sender, and enough context to render it.
#: A partial (`_`-prefixed) is included by another template rather than rendered directly.
RENDERABLE: dict[str, dict[str, object]] = {
    "notification": {
        "title": "Your week in review",
        "body": "Study time: 1.5 hours\nStudy sessions: 3",
        "name": "Ada",
        "action_url": "https://maigie.com/notifications?open=abc",
        "settings_url": "https://maigie.com/settings?tab=notifications",
        "category_reason": "you asked Maigie to email you about your progress",
    },
    "bulk_email": {
        "subject": "Your credit purchase was successful",
        "name": "Ada",
        "content": "<p>Pack: <strong>Small</strong></p>",
    },
    "space_invite": {
        "inviter_name": "Grace",
        "space_name": "Physics",
        "spaces_url": "https://maigie.com/spaces",
    },
    "subscription_success": {
        "name": "Ada",
        "tier_name": "Maigie Plus Monthly",
        "dashboard_url": "https://maigie.com/dashboard",
    },
    "limit_reached": {"name": "Ada", "subscription_url": "https://maigie.com/subscription"},
    "verification": {"name": "Ada", "otp_code": "123456"},
    "welcome": {"name": "Ada", "login_url": "https://maigie.com/login"},
    "reset_password": {"name": "Ada", "otp_code": "654321"},
}

#: Templates deleted with their senders. Named individually because the failure being guarded
#: against is one coming back as a convenient starting point for a new email.
REMOVED = (
    "schedule_reminder",
    "weekly_tips",
    "morning_schedule",
    "credit_purchase_receipt",
)

#: The standalone body styling every old template carried.
_STANDALONE_BODY = "font-family: Helvetica, Arial, sans-serif; color: #333333"


def _render_one(name: str) -> tuple[str, str]:
    data: dict[str, object] = {"app_name": APP_NAME, "logo_url": ""}
    data.update(RENDERABLE[name])
    return _render(name, "fallback text", **data)


@pytest.mark.parametrize("name", sorted(RENDERABLE))
def test_every_email_is_built_from_the_shared_shell(name: str) -> None:
    html, _ = _render_one(name)

    # Structure and palette that only `base.html` supplies.
    assert '<table role="presentation"' in html, f"{name} does not extend base.html"
    assert "#f4f4f7" in html, f"{name} is missing the shared background"
    assert _STANDALONE_BODY not in html, f"{name} still carries the old standalone body styling"


@pytest.mark.parametrize("name", sorted(RENDERABLE))
def test_every_email_has_an_inbox_preview_line(name: str) -> None:
    html, _ = _render_one(name)

    # The hidden preheader is the first thing a learner reads in a crowded inbox. `base.html`
    # renders the block whether or not a template fills it, so an empty one is a silent miss.
    # DOTALL because a preheader built from a notification body can span lines.
    hidden = re.search(
        r"max-height:0; max-width:0; opacity:0; overflow:hidden;\">(.*?)</div>", html, re.DOTALL
    )
    assert hidden is not None, f"{name} lost the preheader container"
    assert hidden.group(1).strip(), f"{name} leaves the inbox preview line empty"


@pytest.mark.parametrize("name", sorted(RENDERABLE))
def test_every_email_has_both_parts_and_no_unrendered_placeholders(name: str) -> None:
    html, text = _render_one(name)

    assert html.strip() and text.strip()
    # A leftover `{{ … }}` means a caller variable was renamed and this template was missed.
    for part in (html, text):
        assert "{{" not in part and "{%" not in part


def test_every_template_on_disk_has_a_sender() -> None:
    stems = {
        path.stem
        for path in FOLDER.iterdir()
        if path.suffix in (".html", ".txt") and not path.name.startswith("_")
    }
    orphans = sorted(stems - set(RENDERABLE) - {"base"})

    assert orphans == [], (
        f"{orphans} have no sender. Delete them, or add them to RENDERABLE with the context "
        "their caller passes — an unrendered template looks like the supported starting point "
        "for the next email."
    )


@pytest.mark.parametrize("stem", REMOVED)
def test_the_templates_whose_senders_were_removed_stay_removed(stem: str) -> None:
    for suffix in (".html", ".txt"):
        assert not (FOLDER / f"{stem}{suffix}").exists()


def test_the_partial_is_still_included_rather_than_orphaned() -> None:
    # `_otp_code.html` has no sender of its own by design; it is shared by the two OTP mails.
    includers = [
        path.name
        for path in FOLDER.glob("*.html")
        if '{% include "_otp_code.html" %}' in path.read_text(encoding="utf-8")
    ]

    assert sorted(includers) == ["reset_password.html", "verification.html"]
