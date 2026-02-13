"""
Text extraction from uploaded files (PDF, DOCX, TXT, MD).
Used for Exam Prep materials to enable RAG and quiz generation.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Max chars to extract (avoid huge payloads)
MAX_EXTRACT_CHARS = 100_000


def extract_text_from_file(
    file_content: bytes,
    filename: str,
    content_type: str | None = None,
) -> str | None:
    """
    Extract text from a file. Supports PDF, DOCX, TXT, MD.
    Returns None if extraction fails or format is unsupported.
    """
    ext = Path(filename).suffix.lower() if filename else ""
    ct = (content_type or "").lower()

    try:
        if ext in (".txt", ".md") or "text/plain" in ct or "text/markdown" in ct:
            return _extract_txt(file_content)

        if ext == ".pdf" or "application/pdf" in ct:
            return _extract_pdf(file_content)

        if ext in (".docx", ".doc") or "application/vnd.openxmlformats" in ct:
            return _extract_docx(file_content)

        # Fallback: try as UTF-8 text
        if ext in (".txt", ".md", ""):
            return _extract_txt(file_content)
    except Exception as e:
        logger.warning("Text extraction failed for %s: %s", filename, e)
        return None

    return None


def _extract_txt(content: bytes) -> str | None:
    """Extract text from plain text / markdown."""
    for encoding in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            text = content.decode(encoding)
            return text[:MAX_EXTRACT_CHARS].strip() or None
        except UnicodeDecodeError:
            continue
    return None


def _extract_pdf(content: bytes) -> str | None:
    """Extract text from PDF using pypdf."""
    try:
        from pypdf import PdfReader
        from io import BytesIO

        reader = PdfReader(BytesIO(content))
        parts = []
        total = 0
        for page in reader.pages:
            if total >= MAX_EXTRACT_CHARS:
                break
            text = page.extract_text()
            if text:
                remaining = MAX_EXTRACT_CHARS - total
                parts.append(text[:remaining])
                total += len(text)
        return "\n\n".join(parts).strip() or None
    except ImportError:
        logger.warning("pypdf not installed, cannot extract PDF")
        return None


def _extract_docx(content: bytes) -> str | None:
    """Extract text from DOCX using python-docx."""
    try:
        from docx import Document
        from io import BytesIO

        doc = Document(BytesIO(content))
        parts = []
        total = 0
        for para in doc.paragraphs:
            if total >= MAX_EXTRACT_CHARS:
                break
            text = para.text.strip()
            if text:
                remaining = MAX_EXTRACT_CHARS - total
                parts.append(text[:remaining])
                total += len(text)
        return "\n\n".join(parts).strip() or None
    except ImportError:
        logger.warning("python-docx not installed, cannot extract DOCX")
        return None
    except Exception as e:
        logger.warning("DOCX extraction failed: %s", e)
        return None
