"""
Document Generation Service.

Generates downloadable documents (PDF, DOCX) from markdown content.
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
    """Generates PDF and DOCX documents from markdown content."""

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
            content: Markdown content to render
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
        """Generate a PDF document from markdown content."""
        from fpdf import FPDF

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=20)
        pdf.add_page()

        # Style configurations
        styles = {
            "academic": {"title_size": 22, "heading_size": 16, "body_size": 11, "margin": 25},
            "report": {"title_size": 24, "heading_size": 18, "body_size": 12, "margin": 20},
            "minimal": {"title_size": 20, "heading_size": 14, "body_size": 11, "margin": 15},
        }
        s = styles.get(style, styles["academic"])

        pdf.set_left_margin(s["margin"])
        pdf.set_right_margin(s["margin"])

        # Title
        pdf.set_font("Helvetica", "B", s["title_size"])
        pdf.multi_cell(0, s["title_size"] * 0.5, self._sanitize_for_pdf(title))
        pdf.ln(8)

        # Date
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(120, 120, 120)
        pdf.cell(0, 5, f"Generated on {datetime.now(UTC).strftime('%B %d, %Y')}")
        pdf.ln(12)
        pdf.set_text_color(0, 0, 0)

        # Separator line
        pdf.set_draw_color(200, 200, 200)
        pdf.line(s["margin"], pdf.get_y(), 210 - s["margin"], pdf.get_y())
        pdf.ln(10)

        # Parse and render markdown content
        self._render_markdown_to_pdf(pdf, content, s)

        return pdf.output()

    def _render_markdown_to_pdf(self, pdf: Any, content: str, style: dict) -> None:
        """Parse markdown and render to PDF with formatting."""
        lines = content.split("\n")
        in_code_block = False
        code_buffer: list[str] = []
        table_buffer: list[str] = []
        in_table = False

        for line in lines:
            # Sanitize unicode characters unsupported by Helvetica
            line = self._sanitize_for_pdf(line)

            # Code blocks
            if line.strip().startswith("```"):
                if in_table:
                    self._render_table_pdf(pdf, table_buffer, style)
                    table_buffer = []
                    in_table = False
                if in_code_block:
                    self._render_code_block_pdf(pdf, "\n".join(code_buffer), style)
                    code_buffer = []
                    in_code_block = False
                else:
                    in_code_block = True
                continue

            if in_code_block:
                code_buffer.append(line)
                continue

            # Table detection: lines starting and containing pipes
            is_table_line = "|" in line and line.strip().startswith("|")
            is_separator = is_table_line and all(
                c in "|-: " for c in line.strip().strip("|")
            )

            if is_table_line:
                if not in_table:
                    in_table = True
                    table_buffer = []
                if not is_separator:
                    table_buffer.append(line)
                continue
            elif in_table:
                # End of table
                self._render_table_pdf(pdf, table_buffer, style)
                table_buffer = []
                in_table = False

            # Horizontal rules
            if line.strip() in ("---", "***", "___"):
                pdf.ln(4)
                y = pdf.get_y()
                pdf.set_draw_color(200, 200, 200)
                pdf.line(pdf.l_margin, y, 210 - pdf.r_margin, y)
                pdf.ln(4)
                continue

            # Headings
            if line.startswith("### "):
                pdf.ln(6)
                pdf.set_font("Helvetica", "B", style["body_size"] + 2)
                pdf.multi_cell(0, 6, self._strip_markdown_inline(line[4:].strip()))
                pdf.ln(3)
            elif line.startswith("## "):
                pdf.ln(8)
                pdf.set_font("Helvetica", "B", style["heading_size"] - 2)
                pdf.multi_cell(0, 7, self._strip_markdown_inline(line[3:].strip()))
                pdf.ln(4)
            elif line.startswith("# "):
                pdf.ln(10)
                pdf.set_font("Helvetica", "B", style["heading_size"])
                pdf.multi_cell(0, 8, self._strip_markdown_inline(line[2:].strip()))
                pdf.ln(5)
            # Bullet points
            elif line.strip().startswith("- ") or line.strip().startswith("* "):
                pdf.set_font("Helvetica", "", style["body_size"])
                indent = len(line) - len(line.lstrip())
                bullet_text = line.strip()[2:]
                pdf.set_x(pdf.l_margin + indent * 2 + 5)
                pdf.cell(4, 5, "\xb7")  # middle dot bullet (latin-1 safe)
                pdf.multi_cell(0, 5, f" {self._strip_markdown_inline(bullet_text)}")
                pdf.ln(1)
            # Numbered lists
            elif re.match(r"^\s*\d+\.\s", line):
                pdf.set_font("Helvetica", "", style["body_size"])
                match = re.match(r"^(\s*\d+\.)\s(.*)", line)
                if match:
                    num = match.group(1)
                    text = match.group(2)
                    pdf.set_x(pdf.l_margin + 5)
                    pdf.cell(10, 5, num)
                    pdf.multi_cell(0, 5, self._strip_markdown_inline(text))
                    pdf.ln(1)
            # Empty line
            elif not line.strip():
                pdf.ln(4)
            # Regular paragraph
            else:
                pdf.set_font("Helvetica", "", style["body_size"])
                clean_text = self._strip_markdown_inline(line)
                if clean_text.strip():
                    pdf.multi_cell(0, 5, clean_text)
                    pdf.ln(2)

        # Flush any remaining table
        if in_table and table_buffer:
            self._render_table_pdf(pdf, table_buffer, style)

    def _render_table_pdf(self, pdf: Any, rows: list[str], style: dict) -> None:
        """Render a markdown table as a properly formatted PDF table with grid lines."""
        if not rows:
            return

        # Parse cells from each row
        parsed_rows: list[list[str]] = []
        for row in rows:
            cells = [
                self._strip_markdown_inline(cell.strip())
                for cell in row.strip().strip("|").split("|")
            ]
            parsed_rows.append(cells)

        if not parsed_rows:
            return

        # Determine column count and available width
        num_cols = max(len(row) for row in parsed_rows)
        available_width = 210 - pdf.l_margin - pdf.r_margin

        # Calculate column widths based on content
        col_widths = [0.0] * num_cols
        for row in parsed_rows:
            for i, cell in enumerate(row):
                if i < num_cols:
                    # Approximate character width at body_size
                    cell_width = pdf.get_string_width(cell) + 4
                    col_widths[i] = max(col_widths[i], cell_width)

        # Scale columns to fit available width
        total_width = sum(col_widths)
        if total_width > available_width:
            scale = available_width / total_width
            col_widths = [w * scale for w in col_widths]
        elif total_width < available_width * 0.5:
            # If table is too narrow, distribute extra space proportionally
            scale = min(available_width / total_width, 1.5)
            col_widths = [w * scale for w in col_widths]
            total_width = sum(col_widths)

        line_height = 6
        pdf.ln(4)

        for row_idx, row in enumerate(parsed_rows):
            # Check if we need a page break
            if pdf.get_y() + line_height > 280:
                pdf.add_page()

            x_start = pdf.get_x()
            y_start = pdf.get_y()

            # Header row styling
            if row_idx == 0:
                pdf.set_font("Helvetica", "B", style["body_size"] - 1)
                pdf.set_fill_color(240, 240, 245)
                fill = True
            else:
                pdf.set_font("Helvetica", "", style["body_size"] - 1)
                # Alternate row shading
                if row_idx % 2 == 0:
                    pdf.set_fill_color(248, 248, 252)
                    fill = True
                else:
                    fill = False

            # Draw cells
            for i in range(num_cols):
                cell_text = row[i] if i < len(row) else ""
                w = col_widths[i] if i < len(col_widths) else 20
                # Truncate if text is too wide for cell
                while pdf.get_string_width(cell_text) > w - 3 and len(cell_text) > 3:
                    cell_text = cell_text[:-4] + "..."
                pdf.cell(w, line_height, cell_text, border=1, fill=fill)

            pdf.ln(line_height)

        pdf.ln(4)

    def _render_code_block_pdf(self, pdf: Any, code: str, style: dict) -> None:
        """Render a code block with a gray background."""
        pdf.ln(3)
        # Gray background
        x = pdf.get_x()
        y = pdf.get_y()
        pdf.set_fill_color(245, 245, 245)
        pdf.set_font("Courier", "", style["body_size"] - 1)

        code_lines = code.split("\n")
        line_height = 4.5
        block_height = len(code_lines) * line_height + 8

        # Draw background rect
        pdf.rect(pdf.l_margin, y, 210 - pdf.l_margin - pdf.r_margin, block_height, "F")

        pdf.set_xy(pdf.l_margin + 4, y + 4)
        for code_line in code_lines:
            pdf.set_x(pdf.l_margin + 4)
            # Truncate very long lines
            if len(code_line) > 90:
                code_line = code_line[:87] + "..."
            pdf.cell(0, line_height, code_line)
            pdf.ln(line_height)

        pdf.ln(6)

    def _strip_markdown_inline(self, text: str) -> str:
        """Remove inline markdown formatting (bold, italic, code, links)."""
        # Bold
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
        text = re.sub(r"__(.+?)__", r"\1", text)
        # Italic
        text = re.sub(r"\*(.+?)\*", r"\1", text)
        text = re.sub(r"_(.+?)_", r"\1", text)
        # Inline code
        text = re.sub(r"`(.+?)`", r"\1", text)
        # Links [text](url) -> text
        text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text)
        return text

    def _sanitize_for_pdf(self, text: str) -> str:
        """Replace Unicode characters unsupported by Helvetica with ASCII equivalents."""
        replacements = {
            "\u2013": "-",   # en-dash
            "\u2014": "--",  # em-dash
            "\u2018": "'",   # left single quote
            "\u2019": "'",   # right single quote
            "\u201c": '"',   # left double quote
            "\u201d": '"',   # right double quote
            "\u2026": "...", # ellipsis
            "\u2022": "-",   # bullet
            "\u00b7": "-",   # middle dot
            "\u2212": "-",   # minus sign
            "\u00a0": " ",   # non-breaking space
            "\u2003": " ",   # em space
            "\u2002": " ",   # en space
            "\u00d7": "x",   # multiplication sign
            "\u00f7": "/",   # division sign
            "\u2264": "<=",  # less than or equal
            "\u2265": ">=",  # greater than or equal
            "\u2260": "!=",  # not equal
            "\u00b0": " deg",  # degree sign
        }
        for char, replacement in replacements.items():
            text = text.replace(char, replacement)
        # Strip any remaining non-latin1 characters
        text = text.encode("latin-1", errors="replace").decode("latin-1")
        return text

    def _generate_docx(self, title: str, content: str, style: str) -> bytes:
        """Generate a DOCX document from markdown content."""
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Inches, Pt, RGBColor

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

        # Parse and render markdown
        self._render_markdown_to_docx(doc, content, s)

        # Save to bytes
        buffer = io.BytesIO()
        doc.save(buffer)
        buffer.seek(0)
        return buffer.getvalue()

    def _render_markdown_to_docx(self, doc: Any, content: str, style: dict) -> None:
        """Parse markdown and render to DOCX with formatting."""
        from docx.shared import Pt, RGBColor

        lines = content.split("\n")
        in_code_block = False
        code_buffer: list[str] = []

        for line in lines:
            # Code blocks
            if line.strip().startswith("```"):
                if in_code_block:
                    self._render_code_block_docx(doc, "\n".join(code_buffer), style)
                    code_buffer = []
                    in_code_block = False
                else:
                    in_code_block = True
                continue

            if in_code_block:
                code_buffer.append(line)
                continue

            # Headings
            if line.startswith("### "):
                doc.add_heading(line[4:].strip(), level=3)
            elif line.startswith("## "):
                doc.add_heading(line[3:].strip(), level=2)
            elif line.startswith("# "):
                doc.add_heading(line[2:].strip(), level=1)
            # Bullet points
            elif line.strip().startswith("- ") or line.strip().startswith("* "):
                text = line.strip()[2:]
                para = doc.add_paragraph(style="List Bullet")
                self._add_formatted_run(para, text, style["body_size"])
            # Numbered lists
            elif re.match(r"^\s*\d+\.\s", line):
                match = re.match(r"^\s*\d+\.\s(.*)", line)
                if match:
                    text = match.group(1)
                    para = doc.add_paragraph(style="List Number")
                    self._add_formatted_run(para, text, style["body_size"])
            # Empty line
            elif not line.strip():
                continue  # DOCX handles spacing via paragraph styles
            # Regular paragraph
            else:
                para = doc.add_paragraph()
                self._add_formatted_run(para, line, style["body_size"])

    def _render_code_block_docx(self, doc: Any, code: str, style: dict) -> None:
        """Render a code block in DOCX with monospace font."""
        from docx.shared import Pt, RGBColor

        para = doc.add_paragraph()
        run = para.add_run(code)
        run.font.name = "Courier New"
        run.font.size = Pt(style["body_size"] - 1)
        # Set paragraph shading (gray background)
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn

        shd = OxmlElement("w:shd")
        shd.set(qn("w:fill"), "F5F5F5")
        shd.set(qn("w:val"), "clear")
        para.paragraph_format.element.get_or_add_pPr().append(shd)

    def _add_formatted_run(self, para: Any, text: str, body_size: int) -> None:
        """Add text to a paragraph with inline markdown formatting."""
        from docx.shared import Pt

        # Simple approach: strip markdown and add as plain text
        # A more advanced version could parse bold/italic segments
        clean_text = self._strip_markdown_inline(text)
        run = para.add_run(clean_text)
        run.font.size = Pt(body_size)

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
