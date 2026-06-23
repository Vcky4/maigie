"""
Document Generation Service.

Generates downloadable documents (PDF, DOCX) from HTML content.
Used by the AI chat to export responses, research, and project work
into formatted documents for students.
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
            content: HTML content to render
            style: Document style ("academic", "report", "minimal")
            user_id: User ID for path namespacing

        Returns:
            dict with keys: filename, url, size, format, content_type
        """
        if len(content) > MAX_CONTENT_LENGTH:
            content = content[:MAX_CONTENT_LENGTH]
            logger.warning(f"Content truncated to {MAX_CONTENT_LENGTH} chars for user {user_id}")

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

        return {
            "filename": filename,
            "url": upload_result["url"],
            "size": upload_result["size"],
            "format": format,
            "content_type": CONTENT_TYPES[format],
            "title": title,
        }

    def _generate_pdf(self, title: str, content: str, style: str) -> bytes:
        """Generate a PDF from HTML content using fpdf2's write_html with Unicode support."""
        from fpdf import FPDF

        # Style configurations
        styles = {
            "academic": {"title_size": 22, "body_size": 11, "margin": 25},
            "report": {"title_size": 24, "body_size": 12, "margin": 20},
            "minimal": {"title_size": 20, "body_size": 11, "margin": 15},
        }
        s = styles.get(style, styles["academic"])

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=20)
        pdf.set_left_margin(s["margin"])
        pdf.set_right_margin(s["margin"])

        # Add DejaVu font for full Unicode support
        # In Docker: /usr/share/fonts/truetype/dejavu/
        # Locally: try common system paths
        font_loaded = self._load_unicode_font(pdf)

        pdf.add_page()

        # Title
        font_name = "DejaVu" if font_loaded else "Helvetica"
        pdf.set_font(font_name, "B", s["title_size"])
        pdf.multi_cell(0, s["title_size"] * 0.5, title)
        pdf.ln(4)

        # Date
        pdf.set_font(font_name, "", 9)
        pdf.set_text_color(120, 120, 120)
        pdf.cell(0, 5, f"Generated on {datetime.now(UTC).strftime('%B %d, %Y')}")
        pdf.ln(8)
        pdf.set_text_color(0, 0, 0)

        # Separator
        pdf.set_draw_color(200, 200, 200)
        pdf.line(s["margin"], pdf.get_y(), 210 - s["margin"], pdf.get_y())
        pdf.ln(8)

        # Set default font for HTML body
        pdf.set_font(font_name, "", s["body_size"])

        # Build the styled HTML wrapper
        html_content = self._wrap_html_with_styles(content, s["body_size"], font_name)

        # If Unicode font not available, sanitize content to ASCII-safe characters
        if not font_loaded:
            html_content = self._sanitize_for_latin1(html_content)

        # Render HTML content
        pdf.write_html(html_content)

        return pdf.output()

    def _load_unicode_font(self, pdf: Any) -> bool:
        """Try to load DejaVu Unicode font from system paths."""
        import os

        # Common paths for DejaVu fonts
        font_dirs = [
            "/usr/share/fonts/truetype/dejavu",  # Debian/Ubuntu Docker
            "/usr/share/fonts/dejavu",  # Some distros
            "C:/Windows/Fonts",  # Windows (has DejaVu if installed)
            "/System/Library/Fonts",  # macOS
        ]

        for font_dir in font_dirs:
            regular = os.path.join(font_dir, "DejaVuSans.ttf")
            bold = os.path.join(font_dir, "DejaVuSans-Bold.ttf")
            if os.path.isfile(regular):
                try:
                    pdf.add_font("DejaVu", "", regular)
                    if os.path.isfile(bold):
                        pdf.add_font("DejaVu", "B", bold)
                    else:
                        pdf.add_font("DejaVu", "B", regular)
                    italic = os.path.join(font_dir, "DejaVuSans-Oblique.ttf")
                    if os.path.isfile(italic):
                        pdf.add_font("DejaVu", "I", italic)
                    return True
                except Exception as e:
                    logger.warning(f"Failed to load DejaVu from {font_dir}: {e}")
                    continue

        logger.warning("DejaVu fonts not found, falling back to Helvetica (limited Unicode)")
        return False

    def _sanitize_for_latin1(self, text: str) -> str:
        """Replace Unicode characters unsupported by Helvetica with ASCII equivalents."""
        replacements = {
            "\u2013": "-",  # en-dash
            "\u2014": "--",  # em-dash
            "\u2018": "'",  # left single quote
            "\u2019": "'",  # right single quote
            "\u201c": '"',  # left double quote
            "\u201d": '"',  # right double quote
            "\u2026": "...",  # ellipsis
            "\u2022": "*",  # bullet
            "\u00b7": "*",  # middle dot
            "\u2212": "-",  # minus sign
            "\u00a0": " ",  # non-breaking space
            "\u2003": " ",  # em space
            "\u2002": " ",  # en space
            "\u00d7": "x",  # multiplication sign
            "\u00f7": "/",  # division sign
            "\u2264": "<=",  # less than or equal
            "\u2265": ">=",  # greater than or equal
            "\u2260": "!=",  # not equal
            "\u2192": "->",  # right arrow
            "\u2190": "<-",  # left arrow
            "\u00b0": " deg",  # degree sign
            "\u2261": "===",  # identical to
        }
        for char, replacement in replacements.items():
            text = text.replace(char, replacement)
        # Strip any remaining non-latin1 characters (preserve HTML tags)
        result = []
        for ch in text:
            try:
                ch.encode("latin-1")
                result.append(ch)
            except UnicodeEncodeError:
                result.append("?")
        return "".join(result)

    def _wrap_html_with_styles(self, content: str, body_size: int, font_name: str) -> str:
        """Wrap raw HTML content with inline style defaults for fpdf2."""
        styled = content

        # Add border attribute to tables if not already present
        styled = re.sub(
            r"<table(?![^>]*border)",
            '<table border="1" cellpadding="4" cellspacing="0"',
            styled,
        )

        # Wrap in a font tag to ensure correct font is used throughout
        styled = f'<font face="{font_name}" size="{body_size}">{styled}</font>'

        return styled

    def _generate_docx(self, title: str, content: str, style: str) -> bytes:
        """Generate a DOCX document from HTML content."""
        from docx import Document
        from docx.shared import Pt, RGBColor

        doc = Document()

        # Style configurations
        styles_config = {
            "academic": {"title_size": 22, "heading_size": 14, "body_size": 11},
            "report": {"title_size": 24, "heading_size": 16, "body_size": 12},
            "minimal": {"title_size": 18, "heading_size": 13, "body_size": 11},
        }
        s = styles_config.get(style, styles_config["academic"])

        # Title
        title_para = doc.add_heading(title, level=0)
        title_run = title_para.runs[0] if title_para.runs else None
        if title_run:
            title_run.font.size = Pt(s["title_size"])

        # Date subtitle
        date_para = doc.add_paragraph()
        date_run = date_para.add_run(f"Generated on {datetime.now(UTC).strftime('%B %d, %Y')}")
        date_run.font.size = Pt(9)
        date_run.font.color.rgb = RGBColor(120, 120, 120)

        # Parse HTML and render to DOCX
        self._render_html_to_docx(doc, content, s)

        # Save to bytes
        buffer = io.BytesIO()
        doc.save(buffer)
        buffer.seek(0)
        return buffer.getvalue()

    def _render_html_to_docx(self, doc: Any, html_content: str, style: dict) -> None:
        """Parse HTML and render to DOCX with proper formatting."""
        from docx.shared import Pt

        # Use a simple tag-based parser for the HTML subset we generate
        # Strip any wrapping tags
        text = html_content

        # Remove HTML tags and convert to structured DOCX
        # This is a simplified parser for the HTML subset the LLM produces
        lines = self._html_to_lines(text)

        for line_type, line_text in lines:
            if line_type == "h1":
                doc.add_heading(line_text, level=1)
            elif line_type == "h2":
                doc.add_heading(line_text, level=2)
            elif line_type == "h3":
                doc.add_heading(line_text, level=3)
            elif line_type == "bullet":
                para = doc.add_paragraph(style="List Bullet")
                run = para.add_run(line_text)
                run.font.size = Pt(style["body_size"])
            elif line_type == "number":
                para = doc.add_paragraph(style="List Number")
                run = para.add_run(line_text)
                run.font.size = Pt(style["body_size"])
            elif line_type == "code":
                para = doc.add_paragraph()
                run = para.add_run(line_text)
                run.font.name = "Courier New"
                run.font.size = Pt(style["body_size"] - 1)
            elif line_type == "table_row":
                # Tables in DOCX require special handling
                # For now, render as tab-separated text
                para = doc.add_paragraph()
                run = para.add_run(line_text)
                run.font.size = Pt(style["body_size"])
            elif line_type == "paragraph" and line_text.strip():
                para = doc.add_paragraph()
                run = para.add_run(line_text)
                run.font.size = Pt(style["body_size"])

    def _html_to_lines(self, html: str) -> list[tuple[str, str]]:
        """Convert HTML to a list of (type, text) tuples for DOCX rendering."""
        lines: list[tuple[str, str]] = []

        # Strip tags helper
        def strip_tags(s: str) -> str:
            return re.sub(r"<[^>]+>", "", s).strip()

        # Extract headings
        html = re.sub(
            r"<h1[^>]*>(.*?)</h1>",
            lambda m: f"\n__H1__{strip_tags(m.group(1))}\n",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        html = re.sub(
            r"<h2[^>]*>(.*?)</h2>",
            lambda m: f"\n__H2__{strip_tags(m.group(1))}\n",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        html = re.sub(
            r"<h3[^>]*>(.*?)</h3>",
            lambda m: f"\n__H3__{strip_tags(m.group(1))}\n",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Extract list items
        html = re.sub(
            r"<li[^>]*>(.*?)</li>",
            lambda m: f"\n__LI__{strip_tags(m.group(1))}\n",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Extract code blocks
        html = re.sub(
            r"<pre[^>]*>(.*?)</pre>",
            lambda m: f"\n__CODE__{strip_tags(m.group(1))}\n",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Extract paragraphs
        html = re.sub(
            r"<p[^>]*>(.*?)</p>",
            lambda m: f"\n__PARA__{strip_tags(m.group(1))}\n",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Extract table rows
        html = re.sub(
            r"<tr[^>]*>(.*?)</tr>",
            lambda m: "\n__TR__"
            + "\t".join(
                strip_tags(cell)
                for cell in re.findall(
                    r"<t[hd][^>]*>(.*?)</t[hd]>", m.group(1), re.DOTALL | re.IGNORECASE
                )
            )
            + "\n",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Track if we're inside an ordered list
        in_ol = False
        ol_counter = 0

        # Check for ordered list context
        ol_regions: list[tuple[int, int]] = []
        for m in re.finditer(r"<ol[^>]*>(.*?)</ol>", html, re.DOTALL | re.IGNORECASE):
            ol_regions.append((m.start(), m.end()))

        # Parse the processed text into lines
        remaining = strip_tags(html)
        for segment in html.split("\n"):
            segment = segment.strip()
            if not segment:
                continue
            if segment.startswith("__H1__"):
                lines.append(("h1", segment[6:]))
            elif segment.startswith("__H2__"):
                lines.append(("h2", segment[6:]))
            elif segment.startswith("__H3__"):
                lines.append(("h3", segment[6:]))
            elif segment.startswith("__LI__"):
                lines.append(("bullet", segment[6:]))
            elif segment.startswith("__CODE__"):
                lines.append(("code", segment[8:]))
            elif segment.startswith("__PARA__"):
                lines.append(("paragraph", segment[8:]))
            elif segment.startswith("__TR__"):
                lines.append(("table_row", segment[6:]))

        return lines

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
