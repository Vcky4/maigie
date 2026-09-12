"""What a tester may attach to a finding, and where it is stored.

Screenshots and short screen recordings, because a bug report about a mobile UI without a picture is a
paragraph asking a triager to imagine something. The limits exist for two different reasons and are
worth keeping apart: the **type** allowlist is about what a triager can actually open, and the **size**
and **count** ceilings are about a storage bill that a paid programme gives strangers an incentive to
run up.

Type checking reads the declared content type, which is a real limitation rather than a defence: it
stops an honest client uploading a PDF and does nothing about a hostile one renaming a binary. The same
caveat applies to `intelligence.conversation.attachments`, and for the same reason — nothing here
executes or renders an upload server-side, and the CDN serves it with the type we recorded.

Copyright (C) 2025 Maigie

Licensed under the Business Source License 1.1 (BUSL-1.1).
See LICENSE file in the repository root for details.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Images a browser and a triager can both open, plus one video container. `video/mp4` alone rather than
#: a family: a tester recording their screen on Android or iOS gets mp4 by default, and accepting `mov`
#: or `webm` as well would mean a triager occasionally cannot play the evidence they are grading.
ALLOWED_CONTENT_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/jpg",
        "image/webp",
        "image/gif",
        "video/mp4",
    }
)

#: 10 MB. Enough for a full-resolution phone screenshot several times over, and for a ten-second screen
#: recording at a sensible bitrate. A tester with a two-minute 4K video has recorded a session rather
#: than a bug, and the report will be better for being cut down.
MAX_BYTES = 10 * 1024 * 1024

#: Three per finding: the screen, the state before it, and the console or error. A fourth is almost
#: always a second bug, which should be a second submission — that is also how it gets paid separately.
MAX_PER_SUBMISSION = 3


@dataclass(frozen=True)
class Rejection:
    """Why an upload was refused, in words the tester reads."""

    code: str
    message: str


def validate(*, content_type: str | None, size: int) -> Rejection | None:
    """Check one upload. `None` means it is acceptable.

    Returns a rejection rather than raising, so the caller decides the status code and so the reason can
    be logged next to the submission it was for.
    """
    if not content_type or content_type.split(";")[0].strip().lower() not in ALLOWED_CONTENT_TYPES:
        return Rejection(
            code="ATTACHMENT_TYPE",
            message="Attach a screenshot (PNG, JPEG, WebP or GIF) or an MP4 screen recording.",
        )
    if size <= 0:
        return Rejection(code="ATTACHMENT_EMPTY", message="That file is empty.")
    if size > MAX_BYTES:
        return Rejection(
            code="ATTACHMENT_TOO_LARGE",
            message=f"Attachments must be under {MAX_BYTES // (1024 * 1024)} MB.",
        )
    return None


def upload_path(*, user_id: str, submission_id: str) -> str:
    """Storage prefix for one submission's attachments.

    Keyed by user **and** submission so that a listing of one tester's uploads is a prefix scan, and so
    an object's path says which finding it belongs to without consulting the database — which is what
    makes an orphaned object identifiable when a retention sweep eventually needs to find one.
    """
    return f"bug-hunt/{user_id}/{submission_id}"
