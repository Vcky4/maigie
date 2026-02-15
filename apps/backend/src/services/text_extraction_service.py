"""
Text extraction from uploaded files (PDF, DOCX, TXT, MD, images).
Used for Exam Prep materials to enable RAG and quiz generation.

Supports OCR for scanned PDFs and images via Google Gemini Vision.
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

        # Image files -> OCR via Gemini Vision
        if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff") or ct.startswith(
            "image/"
        ):
            return _extract_image_text_sync(file_content, ct or f"image/{ext.lstrip('.')}")

        # Fallback: try as UTF-8 text
        if ext in (".txt", ".md", ""):
            return _extract_txt(file_content)
    except Exception as e:
        logger.warning("Text extraction failed for %s: %s", filename, e)
        return None

    return None


async def extract_text_from_file_async(
    file_content: bytes,
    filename: str,
    content_type: str | None = None,
) -> str | None:
    """
    Async version of text extraction. Uses Gemini Vision for images/scanned PDFs.
    """
    ext = Path(filename).suffix.lower() if filename else ""
    ct = (content_type or "").lower()

    try:
        if ext in (".txt", ".md") or "text/plain" in ct or "text/markdown" in ct:
            return _extract_txt(file_content)

        if ext == ".pdf" or "application/pdf" in ct:
            text = _extract_pdf(file_content)
            # If PDF has very little text, it might be scanned -> try OCR
            if text and len(text.strip()) < 100:
                ocr_text = await _extract_image_ocr_via_gemini(file_content, "application/pdf")
                if ocr_text and len(ocr_text) > len(text or ""):
                    return ocr_text
            return text

        if ext in (".docx", ".doc") or "application/vnd.openxmlformats" in ct:
            return _extract_docx(file_content)

        # Image files -> OCR via Gemini Vision
        if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff") or ct.startswith(
            "image/"
        ):
            return await _extract_image_ocr_via_gemini(
                file_content, ct or f"image/{ext.lstrip('.')}"
            )

        # Fallback: try as UTF-8 text
        return _extract_txt(file_content)
    except Exception as e:
        logger.warning("Async text extraction failed for %s: %s", filename, e)
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


def _extract_image_text_sync(content: bytes, mime_type: str) -> str | None:
    """Synchronous fallback for image text extraction. Returns None (OCR needs async Gemini call)."""
    # For sync context, we can't call Gemini. Return None and let async extraction handle it.
    return None


async def _extract_image_ocr_via_gemini(content: bytes, mime_type: str) -> str | None:
    """
    Extract text from images using Google Gemini Vision.
    Handles scanned documents, past question papers, handwritten notes etc.
    """
    try:
        import os
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import google.generativeai as genai

        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

        model = genai.GenerativeModel("gemini-2.0-flash")

        prompt = """Extract ALL text content from this image. This may be a scanned document, 
exam paper, textbook page, or handwritten notes.

IMPORTANT:
- Extract ALL visible text, preserving the structure (headings, numbered lists, questions, etc.)
- If it's an exam paper, clearly extract each question with its number
- If there are multiple choice options, include them with their labels (A, B, C, D)
- Preserve any mathematical notation as best you can
- If text is partially obscured or hard to read, do your best and mark unclear parts with [unclear]
- Return ONLY the extracted text, no commentary

Extracted text:"""

        response = await model.generate_content_async(
            [prompt, {"mime_type": mime_type, "data": content}]
        )

        if response.text:
            return response.text.strip()[:MAX_EXTRACT_CHARS]
        return None

    except Exception as e:
        logger.warning("Gemini Vision OCR failed: %s", e)
        return None
