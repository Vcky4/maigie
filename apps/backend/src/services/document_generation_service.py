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
        pdf.ln(8)

        # Separator
        pdf.set_draw_color(200, 200, 200)
        pdf.line(s["margin"], pdf.get_y(), 210 - s["margin"], pdf.get_y())
        pdf.ln(8)

        # Set default font for HTML body
        pdf.set_font(font_name, "", s["body_size"])

        # Build the styled HTML wrapper
        # Strip escaped apostrophes/quotes from JSON serialization artifacts
        clean_content = content.replace("\\'", "'").replace('\\"', '"')
        html_content = self._wrap_html_with_styles(clean_content, s["body_size"], font_name)

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
        """Generate a DOCX document from HTML content with proper formatting."""
        from html import unescape

        from docx import Document
        from docx.shared import Pt

        doc = Document()

        # Style configurations
        styles_config = {
            "academic": {"title_size": 24, "heading_size": 14, "body_size": 12},
            "report": {"title_size": 22, "heading_size": 14, "body_size": 11},
            "minimal": {"title_size": 18, "heading_size": 13, "body_size": 11},
        }
        s = styles_config.get(style, styles_config["academic"])

        # For academic style, let HTML content handle title page
        # For other styles, add a simple title
        if style != "academic":
            title_para = doc.add_heading(title, level=0)
            if title_para.runs:
                title_para.runs[0].font.size = Pt(s["title_size"])

        # Parse HTML and render to DOCX
        # Strip escaped apostrophes/quotes from JSON serialization artifacts
        clean_content = unescape(content).replace("\\'", "'").replace('\\"', '"')
        self._render_html_to_docx(doc, clean_content, s, style)

        # Save to bytes
        buffer = io.BytesIO()
        doc.save(buffer)
        buffer.seek(0)
        return buffer.getvalue()

    def _render_html_to_docx(
        self, doc: Any, html_content: str, style: dict, doc_style: str
    ) -> None:
        """Parse HTML and render to DOCX with proper tables, page breaks, and formatting."""
        from html import unescape
        from html.parser import HTMLParser

        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Pt

        body_size = style["body_size"]
        service = self  # reference for nested class

        class DocxHTMLParser(HTMLParser):
            """Proper HTML parser that handles nesting correctly."""

            def __init__(self):
                super().__init__()
                self.tag_stack: list[str] = []
                self.content_buffer: list[str] = []
                self.in_list: str | None = None  # "ul" or "ol"
                self.in_table = False
                self.table_html = ""
                self.skip_content = False

            def handle_starttag(self, tag: str, attrs: list) -> None:
                tag = tag.lower()

                if tag == "table":
                    self.in_table = True
                    self.table_html = ""
                    return

                if self.in_table:
                    # Accumulate raw HTML for table processing
                    attr_str = " ".join(f'{k}="{v}"' for k, v in attrs) if attrs else ""
                    self.table_html += f"<{tag} {attr_str}>".strip() if attr_str else f"<{tag}>"
                    return

                if tag == "hr":
                    self._flush_buffer()
                    if doc_style == "academic":
                        doc.add_page_break()
                    else:
                        doc.add_paragraph()
                    return

                if tag == "br":
                    self.content_buffer.append("\n")
                    return

                if tag in ("ul", "ol"):
                    self._flush_buffer()
                    self.in_list = tag
                    return

                if tag == "li":
                    self.content_buffer = []
                    return

                if tag in ("h1", "h2", "h3", "h4", "p", "pre"):
                    self._flush_buffer()
                    self.tag_stack.append(tag)
                    self.content_buffer = []
                    return

                if tag in ("b", "strong", "i", "em", "code"):
                    # Inline formatting - just collect text
                    return

            def handle_endtag(self, tag: str) -> None:
                tag = tag.lower()

                if tag == "table":
                    self.in_table = False
                    service._render_html_table_to_docx(doc, self.table_html, body_size)
                    self.table_html = ""
                    return

                if self.in_table:
                    self.table_html += f"</{tag}>"
                    return

                if tag in ("ul", "ol"):
                    self.in_list = None
                    return

                if tag == "li":
                    text = "".join(self.content_buffer).strip()
                    if text and self.in_list:
                        list_style = "List Bullet" if self.in_list == "ul" else "List Number"
                        para = doc.add_paragraph(style=list_style)
                        run = para.add_run(text)
                        run.font.size = Pt(body_size)
                    self.content_buffer = []
                    return

                if tag in ("h1", "h2", "h3", "h4", "p", "pre"):
                    text = "".join(self.content_buffer).strip()
                    if self.tag_stack and self.tag_stack[-1] == tag:
                        self.tag_stack.pop()

                    if not text:
                        self.content_buffer = []
                        return

                    if tag == "h1":
                        heading = doc.add_heading(text, level=1)
                        if doc_style == "academic":
                            heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    elif tag == "h2":
                        doc.add_heading(text, level=2)
                    elif tag == "h3":
                        doc.add_heading(text, level=3)
                    elif tag == "h4":
                        para = doc.add_paragraph()
                        run = para.add_run(text)
                        run.font.size = Pt(body_size + 1)
                        run.bold = True
                    elif tag == "p":
                        para = doc.add_paragraph()
                        run = para.add_run(text)
                        run.font.size = Pt(body_size)
                    elif tag == "pre":
                        para = doc.add_paragraph()
                        run = para.add_run(text)
                        run.font.name = "Courier New"
                        run.font.size = Pt(body_size - 1)

                    self.content_buffer = []
                    return

            def handle_data(self, data: str) -> None:
                if self.in_table:
                    self.table_html += data
                    return
                self.content_buffer.append(data)

            def handle_entityref(self, name: str) -> None:
                char = unescape(f"&{name};")
                if self.in_table:
                    self.table_html += char
                else:
                    self.content_buffer.append(char)

            def handle_charref(self, name: str) -> None:
                char = unescape(f"&#{name};")
                if self.in_table:
                    self.table_html += char
                else:
                    self.content_buffer.append(char)

            def _flush_buffer(self) -> None:
                """Flush any pending text as a paragraph."""
                text = "".join(self.content_buffer).strip()
                if text and not self.tag_stack and not self.in_list:
                    para = doc.add_paragraph()
                    run = para.add_run(text)
                    run.font.size = Pt(body_size)
                self.content_buffer = []

        parser = DocxHTMLParser()
        parser.feed(html_content)
        parser._flush_buffer()  # flush any trailing content

    def _render_html_table_to_docx(self, doc: Any, table_html: str, body_size: int) -> None:
        """Render an HTML table as a proper Word table with grid borders."""
        from html import unescape

        from docx.shared import Pt

        def strip_tags(s: str) -> str:
            return unescape(re.sub(r"<[^>]+>", "", s)).strip()

        # Extract header rows
        header_rows: list[list[str]] = []
        thead_match = re.search(r"<thead[^>]*>(.*?)</thead>", table_html, re.DOTALL | re.IGNORECASE)
        if thead_match:
            for tr in re.findall(
                r"<tr[^>]*>(.*?)</tr>",
                thead_match.group(1),
                re.DOTALL | re.IGNORECASE,
            ):
                cells = [
                    strip_tags(c)
                    for c in re.findall(
                        r"<t[hd][^>]*>(.*?)</t[hd]>",
                        tr,
                        re.DOTALL | re.IGNORECASE,
                    )
                ]
                if cells:
                    header_rows.append(cells)

        # Extract body rows
        body_rows: list[list[str]] = []
        tbody_match = re.search(r"<tbody[^>]*>(.*?)</tbody>", table_html, re.DOTALL | re.IGNORECASE)
        tbody_content = tbody_match.group(1) if tbody_match else table_html
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", tbody_content, re.DOTALL | re.IGNORECASE):
            cells = [
                strip_tags(c)
                for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.DOTALL | re.IGNORECASE)
            ]
            if cells and cells not in header_rows:
                body_rows.append(cells)

        all_rows = header_rows + body_rows
        if not all_rows:
            return

        num_cols = max(len(row) for row in all_rows)
        num_rows = len(all_rows)

        # Create Word table with grid style
        table = doc.add_table(rows=num_rows, cols=num_cols)
        table.style = "Table Grid"

        # Populate cells
        for row_idx, row_data in enumerate(all_rows):
            row_cells = table.rows[row_idx].cells
            for col_idx, cell_text in enumerate(row_data):
                if col_idx < num_cols:
                    row_cells[col_idx].text = cell_text
                    for paragraph in row_cells[col_idx].paragraphs:
                        for run in paragraph.runs:
                            run.font.size = Pt(body_size - 1)
                            if row_idx < len(header_rows):
                                run.bold = True

        doc.add_paragraph()  # spacing after table

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

        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, write=30.0)) as client:
            response = await client.put(upload_url, headers=headers, content=bytes(content))
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
