"""Filename hygiene and text extraction for learner-uploaded files.

Both of these started life private to `personal_learning.services.exam_prep_service`,
where preparation materials are uploaded. They are here because course materials now
need the identical treatment: a course outline can only be grounded in an uploaded
syllabus if something reads the bytes, and a course-scoped storage path is exactly as
vulnerable to `../` in a client-supplied filename as a preparation-scoped one.

Lifting rather than copying matters for one specific reason. If two extractors exist,
one of them eventually learns a new format — DOCX, say — and the other silently does
not, so the same file grounds a preparation and is ignored by a course. One
implementation cannot drift from itself.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Uploads are capped well below what a textbook would be. The cap exists because
#: extraction reads the whole file into memory, and because a 200MB scan is not
#: material a learner is going to revise from.
MAX_MATERIAL_UPLOAD_BYTES = 25 * 1024 * 1024

#: Text is extracted for these, so downstream generation has something to read. Other
#: types are stored and downloadable but contribute nothing to extraction, and the API
#: reports that rather than leaving the client to guess from the extension.
TEXT_EXTENSIONS = (".txt", ".md", ".markdown", ".csv")


def safe_filename(raw: str | None) -> str:
    """Reduce a client-supplied filename to something safe to use as a path segment.

    Only the basename is kept and the character set is restricted, so a name like
    `../../other-user/notes.pdf` cannot write outside its owner's own prefix.
    """
    import re

    candidate = (raw or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "_", candidate).strip("._")
    return candidate[:200] or "material"


def extract_upload_text(content: bytes, filename: str, content_type: str | None) -> str | None:
    """Pull readable text out of an uploaded file, or return None.

    Returning `None` is a normal outcome, not an error: an image or a slide deck is
    still worth storing. Extraction failure is also `None` rather than an exception,
    because a file the learner can open is worth keeping even if we cannot read it.
    """
    lowered = filename.lower()

    if lowered.endswith(TEXT_EXTENSIONS) or (content_type or "").startswith("text/"):
        try:
            return content.decode("utf-8", errors="replace").strip() or None
        except Exception:  # noqa: BLE001 - a file we cannot decode is still storable
            return None

    if lowered.endswith(".pdf") or (content_type or "") == "application/pdf":
        try:
            import io

            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(content))
            pages = [page.extract_text() or "" for page in reader.pages]
            text = "\n\n".join(part.strip() for part in pages if part.strip())
            return text or None
        except Exception as e:  # noqa: BLE001 - a scanned PDF has no text layer
            logger.info(
                "PDF text extraction produced nothing",
                extra={"filename": filename, "error": type(e).__name__},
            )
            return None

    return None
