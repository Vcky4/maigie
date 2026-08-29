"""Images and audio a learner attaches to a turn.

Plan §6.1 and §5.2.2. The web composer had an attach button that **collected images and silently dropped
them** — no endpoint existed — and mobile's hook targeted `/api/v1/chat/*`, a prefix that has never
existed. So an affordance that looked like it worked never sent anything, which is §1's territory: the
button asserted a capability the product did not have.

**Why validation is a pure function here.** These files are learner-supplied and reach a model, so what
is accepted is a security decision and not a detail of the route. Kept separate from the upload so it can
be tested without storage, and so both the image and audio paths refuse on the same rules rather than
drifting into two policies.

`UserUpload` already had every column this needs — url, filename, mimeType, size, chatMessageId — so
nothing here is a new table. The row is what makes an attachment attributable: without it an image in
storage has no owner and no turn.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: The largest attachment accepted, in bytes.
#:
#: Ten megabytes. A phone photo is one to five, a screenshot under one, so this clears real use with room
#: to spare. What it stops is the upload that is not a picture of anything — a video renamed, a
#: multi-hundred-megabyte scan — where the cost lands on the storage bill and on a model call that will
#: fail anyway.
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024

#: Image types the model can actually read.
#:
#: An allowlist rather than a blocklist, because the failure directions are not symmetric: an unlisted
#: format the model happens to support is a learner told "not supported", while a listed format it cannot
#: read is a turn that fails after the upload succeeded and the credits are spent. The first is a
#: correctable annoyance; the second charges for nothing.
#:
#: SVG is deliberately absent. It is a document that can carry script, not a bitmap, and no model reads it
#: as an image — accepting it would store an XSS payload under a URL this product serves.
ALLOWED_IMAGE_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/webp", "image/gif", "image/heic", "image/heif"}
)

#: Audio types accepted for transcription. Narrow on purpose: this is what the transcription provider
#: takes, and widening it means a file that uploads and then cannot be transcribed.
ALLOWED_AUDIO_TYPES = frozenset(
    {"audio/webm", "audio/ogg", "audio/mpeg", "audio/mp4", "audio/wav", "audio/x-m4a"}
)

ATTACHMENT_REJECTED_EMPTY = "attachment_empty"
ATTACHMENT_REJECTED_TOO_LARGE = "attachment_too_large"
ATTACHMENT_REJECTED_TYPE = "attachment_type_not_supported"


@dataclass(frozen=True, slots=True)
class AttachmentRejection:
    """Why a file was not accepted."""

    code: str
    message: str


def validate_attachment(
    *, content_type: str | None, size: int, allowed: frozenset[str]
) -> AttachmentRejection | None:
    """Check a file is worth storing, or say why not.

    **Type is read from the declared content type, and that is a known weakness stated rather than
    hidden.** A client can declare anything, so this is not a proof of what the bytes are — it is enough
    to keep honest clients from sending what the model cannot read, and it is not a defence against a
    hostile one. Sniffing the magic bytes would be, and it is the right follow-up; what makes the gap
    tolerable today is that these files are served back as attachments rather than executed, and that SVG
    — the one type where a mislabelled file is actively dangerous — is not on the allowlist at all.

    Size is checked against bytes already read rather than the `Content-Length` header, so it cannot be
    understated by a client.
    """
    if size <= 0:
        return AttachmentRejection(
            code=ATTACHMENT_REJECTED_EMPTY, message="That file appears to be empty."
        )
    if size > MAX_ATTACHMENT_BYTES:
        return AttachmentRejection(
            code=ATTACHMENT_REJECTED_TOO_LARGE,
            message=(
                f"That file is {size / 1_048_576:.1f} MB and the limit is "
                f"{MAX_ATTACHMENT_BYTES // 1_048_576} MB."
            ),
        )
    if (content_type or "").split(";")[0].strip().lower() not in allowed:
        return AttachmentRejection(
            code=ATTACHMENT_REJECTED_TYPE,
            message=f"{content_type or 'That file type'} is not supported here.",
        )
    return None


def upload_path(*, user_id: str, kind: str) -> str:
    """Where an attachment is stored.

    Namespaced by learner so a listing is per-account and a deletion cannot walk into someone else's
    folder by filename. `kind` separates images from audio, because their retention will differ: an image
    is part of the conversation, where audio is an input to transcription and has no reason to outlive it.
    """
    return f"ask/{kind}/{user_id}"


def build_upload_row(
    *,
    user_id: str,
    url: str,
    filename: str,
    mime_type: str | None,
    size: int | None,
) -> dict[str, Any]:
    """The `UserUpload` row for a stored attachment, in the repository's wire shape.

    Returned rather than written, like `ask_service.build_assistant_row`. `chatMessageId` is deliberately
    absent: the attachment is uploaded *before* the turn that carries it exists, so linking it is the
    turn's job and a `None` here would be indistinguishable from an orphan that was never used.
    """
    return {
        "userId": user_id,
        "url": url,
        "filename": filename,
        "mimeType": mime_type,
        "size": size,
    }
