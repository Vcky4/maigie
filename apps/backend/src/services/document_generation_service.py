"""
Document Generation Service.

Generates downloadable documents (PDF, DOCX) from HTML content.
Uses WeasyPrint for PDF rendering and htmldocx for DOCX conversion.
"""

from __future__ import annotations

import io
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Content type mappings
CONTENT_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

# Max content length to prevent abuse (100k chars ~= 50 pages)
MAX_CONTENT_LENGTH = 100_000

# CSS styles for different document styles
_ACADEMIC_CSS = """
@page {
    size: A4;
    margin: 2.5cm 2cm;
}
body {
    font-family: 'Times New Roman', Times, Georgia, serif;
    font-size: 12pt;
    line-height: 1.6;
    color: #1a1a1a;
}
h1 { font-size: 22pt; margin-top: 1.5em; margin-bottom: 0.5em; color: #111; }
h2 { font-size: 16pt; margin-top: 1.3em; margin-bottom: 0.4em; color: #222; }
h3 { font-size: 13pt; margin-top: 1.1em; margin-bottom: 0.3em; color: #333; }
h4 { font-size: 12pt; margin-top: 1em; margin-bottom: 0.3em; font-style: italic; }
p { margin-bottom: 0.8em; text-align: justify; }
ul, ol { margin-bottom: 0.8em; padding-left: 2em; }
li { margin-bottom: 0.3em; }
blockquote {
    border-left: 3px solid #ccc;
    margin-left: 0;
    padding-left: 1em;
    color: #555;
    font-style: italic;
}
code {
    font-family: 'Courier New', monospace;
    font-size: 10pt;
    background: #f5f5f5;
    padding: 1px 4px;
}
pre {
    background: #f5f5f5;
    padding: 12px;
    font-size: 10pt;
    line-height: 1.4;
    white-space: pre-wrap;
    word-wrap: break-word;
}
pre code { background: none; padding: 0; }
table {
    width: 100%;
    border-collapse: collapse;
    margin: 1em 0;
    font-size: 11pt;
}
th, td {
    border: 1px solid #ccc;
    padding: 8px 10px;
    text-align: left;
}
th { background: #f0f0f0; font-weight: bold; }
hr { border: none; border-top: 1px solid #ddd; margin: 2em 0; }
"""

_REPORT_CSS = """
@page {
    size: A4;
    margin: 2cm;
}
body {
    font-family: Helvetica, Arial, sans-serif;
    font-size: 11pt;
    line-height: 1.5;
    color: #2d2d2d;
}
h1 { font-size: 24pt; margin-top: 1.2em; margin-bottom: 0.4em; color: #1a1a1a; font-weight: bold; }
h2 { font-size: 16pt; margin-top: 1.2em; margin-bottom: 0.4em; color: #333; border-bottom: 1px solid #eee; padding-bottom: 4px; }
h3 { font-size: 13pt; margin-top: 1em; margin-bottom: 0.3em; color: #444; }
h4 { font-size: 11pt; margin-top: 0.8em; margin-bottom: 0.3em; color: #555; font-weight: bold; }
p { margin-bottom: 0.7em; }
ul, ol { margin-bottom: 0.7em; padding-left: 1.8em; }
li { margin-bottom: 0.2em; }
blockquote {
    border-left: 4px solid #4f46e5;
    margin-left: 0;
    padding-left: 1em;
    color: #555;
}
code {
    font-family: 'Courier New', monospace;
    font-size: 9.5pt;
    background: #f8f8f8;
    padding: 2px 5px;
}
pre {
    background: #f8f8f8;
    padding: 14px;
    font-size: 9.5pt;
    line-height: 1.4;
    border: 1px solid #e8e8e8;
    white-space: pre-wrap;
    word-wrap: break-word;
}
pre code { background: none; padding: 0; border: none; }
table {
    width: 100%;
    border-collapse: collapse;
    margin: 1em 0;
    font-size: 10pt;
}
th, td {
    border: 1px solid #ddd;
    padding: 8px 12px;
    text-align: left;
}
th { background: #f5f5f5; font-weight: bold; }
hr { border: none; border-top: 2px solid #eee; margin: 1.5em 0; }
"""

_MINIMAL_CSS = """
@page {
    size: A4;
    margin: 1.5cm;
}
body {
    font-family: Helvetica, Arial, sans-serif;
    font-size: 10.5pt;
    line-height: 1.45;
    color: #333;
}
h1 { font-size: 18pt; margin-top: 1em; margin-bottom: 0.3em; }
h2 { font-size: 14pt; margin-top: 0.9em; margin-bottom: 0.3em; }
h3 { font-size: 12pt; margin-top: 0.8em; margin-bottom: 0.2em; }
p { margin-bottom: 0.5em; }
ul, ol { margin-bottom: 0.5em; padding-left: 1.5em; }
li { margin-bottom: 0.15em; }
code { font-family: monospace; font-size: 9.5pt; background: #f5f5f5; padding: 1px 3px; }
pre { background: #f5f5f5; padding: 10px; font-size: 9pt; white-space: pre-wrap; word-wrap: break-word; }
pre code { background: none; padding: 0; }
table { width: 100%; border-collapse: collapse; margin: 0.8em 0; font-size: 9.5pt; }
th, td { border: 1px solid #ddd; padding: 6px 8px; }
th { background: #f0f0f0; font-weight: bold; }
hr { border: none; border-top: 1px solid #ddd; margin: 1em 0; }
"""

_STYLE_CSS = {
    "academic": _ACADEMIC_CSS,
    "report": _REPORT_CSS,
    "minimal": _MINIMAL_CSS,
}


class DocumentGenerationService:
    """Generates PDF and DOCX documents from HTML content."""

    def __init__(self):
        pass

    async def generate_document(
        self,
        format: str,
        title: str,
        content: str,
        style: str = "academic",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Generate a document in the specified format.

        Args:
            format: Document format ("pdf" or "docx")
            title: Document title
            content: HTML content to render (also handles markdown via conversion)
            style: Document style ("academic", "report", "minimal")
            user_id: User ID for path namespacing

        Returns:
            dict with keys: filename, url, size, format, content_type
        """
        if len(content) > MAX_CONTENT_LENGTH:
            content = content[:MAX_CONTENT_LENGTH]
            logger.warning(f"Content truncated to {MAX_CONTENT_LENGTH} chars for user {user_id}")

        # Normalize content: if it's markdown, convert to HTML
        content = self._ensure_html(content)

        format = format.lower().strip()
        if format not in ("pdf", "docx"):
            raise ValueError(f"Unsupported format: {format}. Use 'pdf' or 'docx'.")

        # Generate the document bytes
        if format == "pdf":
            doc_bytes = self._generate_pdf(title, content, style)
        else:
            doc_bytes = self._generate_docx(title, content, style)

        # Generate a unique filename
        safe_title = re.sub(r"[^\w\s-]", "", title)[:50].strip().replace(" ", "_")
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        short_id = uuid.uuid4().hex[:6]
        filename = f"{safe_title}_{timestamp}_{short_id}.{format}"

        # Upload to storage
        from src.services.storage_service import storage_service

        storage_path = f"generated-docs/{user_id or 'anonymous'}"
        upload_result = await self._upload_bytes(storage_service, doc_bytes, filename, storage_path)

        # Also upload the styled HTML for in-app preview
        full_html = self._build_full_html(title, content, style)
        html_filename = f"{safe_title}_{timestamp}_{short_id}.html"
        preview_result = await self._upload_bytes(
            storage_service, full_html.encode("utf-8"), html_filename, storage_path
        )

        return {
            "filename": filename,
            "url": upload_result["url"],
            "size": upload_result["size"],
            "format": format,
            "content_type": CONTENT_TYPES[format],
            "title": title,
            "preview_url": preview_result["url"],
        }

    def _ensure_html(self, content: str) -> str:
        """If content is markdown, convert to HTML. If already HTML, return as-is."""
        # Detect if content is already HTML (has block-level HTML tags)
        if re.search(r"<(h[1-6]|p|ul|ol|li|table|div|pre|blockquote)\b", content, re.IGNORECASE):
            return content

        # Content is markdown — convert to HTML
        return self._markdown_to_html(content)

    def _markdown_to_html(self, md: str) -> str:
        """Convert markdown to HTML for document rendering."""
        lines = md.split("\n")
        html_parts: list[str] = []
        in_code_block = False
        code_buffer: list[str] = []
        in_list = False
        list_type = ""
        paragraph_buffer: list[str] = []

        def flush_paragraph():
            if paragraph_buffer:
                text = " ".join(paragraph_buffer)
                text = self._inline_md_to_html(text)
                html_parts.append(f"<p>{text}</p>")
                paragraph_buffer.clear()

        def flush_list():
            nonlocal in_list, list_type
            if in_list:
                html_parts.append(f"</{list_type}>")
                in_list = False
                list_type = ""

        for line in lines:
            # Code blocks
            if line.strip().startswith("```"):
                if in_code_block:
                    html_parts.append(
                        f"<pre><code>{self._escape_html(chr(10).join(code_buffer))}</code></pre>"
                    )
                    code_buffer = []
                    in_code_block = False
                else:
                    flush_paragraph()
                    flush_list()
                    in_code_block = True
                continue

            if in_code_block:
                code_buffer.append(line)
                continue

            # Headings
            heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
            if heading_match:
                flush_paragraph()
                flush_list()
                level = len(heading_match.group(1))
                text = self._inline_md_to_html(heading_match.group(2))
                html_parts.append(f"<h{level}>{text}</h{level}>")
                continue

            # Horizontal rule
            if re.match(r"^(\-{3,}|\*{3,}|_{3,})\s*$", line):
                flush_paragraph()
                flush_list()
                html_parts.append("<hr>")
                continue

            # Bullet lists
            bullet_match = re.match(r"^(\s*)([-*+])\s+(.+)$", line)
            if bullet_match:
                flush_paragraph()
                if not in_list or list_type != "ul":
                    flush_list()
                    html_parts.append("<ul>")
                    in_list = True
                    list_type = "ul"
                text = self._inline_md_to_html(bullet_match.group(3))
                html_parts.append(f"<li>{text}</li>")
                continue

            # Numbered lists
            numbered_match = re.match(r"^(\s*)\d+\.\s+(.+)$", line)
            if numbered_match:
                flush_paragraph()
                if not in_list or list_type != "ol":
                    flush_list()
                    html_parts.append("<ol>")
                    in_list = True
                    list_type = "ol"
                text = self._inline_md_to_html(numbered_match.group(2))
                html_parts.append(f"<li>{text}</li>")
                continue

            # Blockquotes
            if line.startswith("> "):
                flush_paragraph()
                flush_list()
                text = self._inline_md_to_html(line[2:])
                html_parts.append(f"<blockquote><p>{text}</p></blockquote>")
                continue

            # Empty line
            if not line.strip():
                flush_paragraph()
                flush_list()
                continue

            # Regular text — accumulate for paragraph
            flush_list()
            paragraph_buffer.append(line.strip())

        # Flush remaining
        flush_paragraph()
        flush_list()
        if in_code_block and code_buffer:
            html_parts.append(
                f"<pre><code>{self._escape_html(chr(10).join(code_buffer))}</code></pre>"
            )

        return "\n".join(html_parts)

    def _inline_md_to_html(self, text: str) -> str:
        """Convert inline markdown (bold, italic, code, links) to HTML."""
        # Bold
        text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
        text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)
        # Italic
        text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
        text = re.sub(r"_(.+?)_", r"<i>\1</i>", text)
        # Inline code
        text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
        # Links
        text = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', text)
        return text

    def _escape_html(self, text: str) -> str:
        """Escape HTML special characters."""
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _build_full_html(self, title: str, body_html: str, style: str) -> str:
        """Wrap body HTML with full document structure and CSS."""
        css = _STYLE_CSS.get(style, _ACADEMIC_CSS)
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{self._escape_html(title)}</title>
<style>{css}</style>
</head>
<body>
{body_html}
</body>
</html>"""

    def _generate_pdf(self, title: str, content: str, style: str) -> bytes:
        """Generate a PDF document from HTML content using xhtml2pdf."""
        from xhtml2pdf import pisa

        full_html = self._build_full_html(title, content, style)
        buffer = io.BytesIO()
        pisa_status = pisa.CreatePDF(io.StringIO(full_html), dest=buffer)

        if pisa_status.err:
            logger.error(f"xhtml2pdf error count: {pisa_status.err}")

        buffer.seek(0)
        return buffer.getvalue()

    def _generate_docx(self, title: str, content: str, style: str) -> bytes:
        """Generate a DOCX document from HTML content using htmldocx."""
        from docx import Document
        from docx.shared import Pt
        from htmldocx import HtmlToDocx

        doc = Document()
        parser = HtmlToDocx()

        # Add the HTML content
        parser.add_html_to_document(content, doc)

        # Save to bytes
        buffer = io.BytesIO()
        doc.save(buffer)
        buffer.seek(0)
        return buffer.getvalue()

    async def _upload_bytes(
        self, storage_service: Any, content: bytes, filename: str, path: str
    ) -> dict[str, Any]:
        """Upload raw bytes to BunnyCDN storage."""
        import httpx

        if not storage_service.api_key or not storage_service.storage_zone:
            raise RuntimeError("Storage configuration is missing.")

        upload_path = f"{path.strip('/')}/{filename}"
        upload_url = f"{storage_service.base_url}/{upload_path}"

        headers = {
            "AccessKey": storage_service.api_key,
            "Content-Type": "application/octet-stream",
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.put(upload_url, headers=headers, content=content)
            if response.status_code != 201:
                raise RuntimeError(f"Upload failed: {response.status_code} - {response.text}")

        # Build public URL
        if storage_service.public_url_base:
            public_url = f"{storage_service.public_url_base}/{upload_path}"
        else:
            public_url = f"https://{storage_service.cdn_hostname}/{upload_path}"

        return {"filename": filename, "url": public_url, "size": len(content)}


# Module-level singleton
document_generation_service = DocumentGenerationService()
