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
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
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

        format = format.lower().strip()
        if format not in ("pdf", "docx", "pptx"):
            raise ValueError(f"Unsupported format: {format}. Use 'pdf', 'docx', or 'pptx'.")

        # For pptx, content is a JSON string of slides; for pdf/docx it's HTML/markdown
        if format == "pptx":
            doc_bytes = self._generate_pptx(title, content, style)
            preview_html = self._build_pptx_preview_html(title, content, style)
        else:
            # Normalize content: if it's markdown, convert to HTML
            content = self._ensure_html(content)
            if format == "pdf":
                doc_bytes = self._generate_pdf(title, content, style)
            else:
                doc_bytes = self._generate_docx(title, content, style)
            preview_html = self._build_full_html(title, content, style)

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
        html_filename = f"{safe_title}_{timestamp}_{short_id}.html"
        preview_result = await self._upload_bytes(
            storage_service, preview_html.encode("utf-8"), html_filename, storage_path
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

    def _generate_pptx(self, title: str, content: str, style: str) -> bytes:
        """Generate a styled PPTX presentation from content."""
        from pptx import Presentation
        from pptx.dml.color import RGBColor
        from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
        from pptx.util import Emu, Inches, Pt

        prs = Presentation()
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)

        # Color scheme
        primary = RGBColor(0x4F, 0x46, 0xE5)  # Indigo
        dark = RGBColor(0x1A, 0x1A, 0x2E)
        white = RGBColor(0xFF, 0xFF, 0xFF)
        light_bg = RGBColor(0xF8, 0xFA, 0xFC)
        text_dark = RGBColor(0x1A, 0x1A, 0x1A)
        text_gray = RGBColor(0x55, 0x55, 0x55)

        slides_data = self._extract_slides_from_content(content, title)

        for i, slide_data in enumerate(slides_data):
            slide_title = slide_data.get("title", f"Slide {i + 1}")
            bullets = slide_data.get("bullets", [])
            subtitle = slide_data.get("subtitle", "")

            # Use blank layout for full control
            layout = prs.slide_layouts[6]  # Blank
            slide = prs.slides.add_slide(layout)

            if i == 0:
                # === TITLE SLIDE ===
                # Dark background
                bg = slide.background
                fill = bg.fill
                fill.solid()
                fill.fore_color.rgb = dark

                # Accent bar at top
                bar = slide.shapes.add_shape(1, Inches(0), Inches(0), prs.slide_width, Inches(0.15))
                bar.fill.solid()
                bar.fill.fore_color.rgb = primary
                bar.line.fill.background()

                # Title text
                title_box = slide.shapes.add_textbox(
                    Inches(1.5), Inches(2.2), Inches(10.3), Inches(2)
                )
                tf = title_box.text_frame
                tf.word_wrap = True
                p = tf.paragraphs[0]
                p.text = slide_title
                p.font.size = Pt(44)
                p.font.bold = True
                p.font.color.rgb = white
                p.alignment = PP_ALIGN.LEFT

                # Subtitle
                if subtitle:
                    sub_box = slide.shapes.add_textbox(
                        Inches(1.5), Inches(4.4), Inches(10.3), Inches(1.2)
                    )
                    tf2 = sub_box.text_frame
                    tf2.word_wrap = True
                    p2 = tf2.paragraphs[0]
                    p2.text = subtitle
                    p2.font.size = Pt(22)
                    p2.font.color.rgb = RGBColor(0xA0, 0xA0, 0xC0)
                    p2.alignment = PP_ALIGN.LEFT
                elif bullets:
                    sub_box = slide.shapes.add_textbox(
                        Inches(1.5), Inches(4.4), Inches(10.3), Inches(1.2)
                    )
                    tf2 = sub_box.text_frame
                    tf2.word_wrap = True
                    p2 = tf2.paragraphs[0]
                    p2.text = bullets[0]
                    p2.font.size = Pt(20)
                    p2.font.color.rgb = RGBColor(0xA0, 0xA0, 0xC0)

                # Bottom accent line
                line = slide.shapes.add_shape(1, Inches(1.5), Inches(6.8), Inches(3), Inches(0.06))
                line.fill.solid()
                line.fill.fore_color.rgb = primary
                line.line.fill.background()

            else:
                # === CONTENT SLIDE ===
                # Light background
                bg = slide.background
                fill = bg.fill
                fill.solid()
                fill.fore_color.rgb = white

                # Colored header bar
                header_bar = slide.shapes.add_shape(
                    1, Inches(0), Inches(0), prs.slide_width, Inches(1.4)
                )
                header_bar.fill.solid()
                header_bar.fill.fore_color.rgb = primary
                header_bar.line.fill.background()

                # Title on the header bar
                title_box = slide.shapes.add_textbox(
                    Inches(0.8), Inches(0.3), Inches(11.5), Inches(0.9)
                )
                tf = title_box.text_frame
                tf.word_wrap = True
                tf.vertical_anchor = MSO_ANCHOR.MIDDLE
                p = tf.paragraphs[0]
                p.text = slide_title
                p.font.size = Pt(28)
                p.font.bold = True
                p.font.color.rgb = white
                p.alignment = PP_ALIGN.LEFT

                # Content area with bullets
                if bullets:
                    content_box = slide.shapes.add_textbox(
                        Inches(0.8), Inches(1.8), Inches(11.5), Inches(5.2)
                    )
                    tf = content_box.text_frame
                    tf.word_wrap = True

                    for j, bullet in enumerate(bullets):
                        if j == 0:
                            para = tf.paragraphs[0]
                        else:
                            para = tf.add_paragraph()

                        para.text = bullet
                        para.font.size = Pt(20)
                        para.font.color.rgb = text_dark
                        para.space_after = Pt(12)
                        # Add bullet character
                        para.level = 0

                # Slide number
                num_box = slide.shapes.add_textbox(
                    Inches(12.2), Inches(7.0), Inches(0.8), Inches(0.4)
                )
                ntf = num_box.text_frame
                np = ntf.paragraphs[0]
                np.text = str(i + 1)
                np.font.size = Pt(11)
                np.font.color.rgb = text_gray
                np.alignment = PP_ALIGN.RIGHT

        buffer = io.BytesIO()
        prs.save(buffer)
        buffer.seek(0)
        return buffer.getvalue()

    def _extract_slides_from_content(self, content: str, title: str) -> list[dict]:
        """Extract slide data from content (HTML sections, JSON, or plain text)."""
        import json

        # Try JSON first
        try:
            data = json.loads(content)
            if isinstance(data, list) and len(data) > 0:
                return data
            if isinstance(data, dict) and "slides" in data:
                return data["slides"]
        except (json.JSONDecodeError, TypeError):
            pass

        # Try HTML <section> format
        if "<section" in content:
            return self._parse_sections_to_slides(content)

        # Fallback: use the legacy parser
        return self._parse_pptx_content(content, title)

    def _parse_sections_to_slides(self, html: str) -> list[dict]:
        """Parse HTML <section> tags into slide data for PPTX."""
        slides: list[dict] = []
        # Split by section tags
        sections = re.split(r"<section[^>]*>", html)

        for section in sections:
            if not section.strip():
                continue
            # Remove closing tag
            section = re.sub(r"</section>.*", "", section, flags=re.DOTALL)

            # Extract title from h1 or h2
            title_match = re.search(r"<h[12][^>]*>(.*?)</h[12]>", section, re.DOTALL)
            slide_title = ""
            if title_match:
                slide_title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip()

            # Extract subtitle from first <p> after title
            subtitle = ""
            subtitle_match = re.search(r"</h[12]>\s*<p[^>]*>(.*?)</p>", section, re.DOTALL)
            if subtitle_match:
                subtitle = re.sub(r"<[^>]+>", "", subtitle_match.group(1)).strip()

            # Extract bullet points from <li> tags
            bullets: list[str] = []
            for li_match in re.finditer(r"<li[^>]*>(.*?)</li>", section, re.DOTALL):
                text = re.sub(r"<[^>]+>", "", li_match.group(1)).strip()
                if text:
                    bullets.append(text)

            # If no bullets, extract <p> content as bullets (skip subtitle)
            if not bullets:
                p_tags = re.findall(r"<p[^>]*>(.*?)</p>", section, re.DOTALL)
                for p in p_tags:
                    text = re.sub(r"<[^>]+>", "", p).strip()
                    if text and text != subtitle:
                        bullets.append(text)

            if slide_title or bullets:
                slide: dict = {"title": slide_title or "Untitled", "bullets": bullets}
                if subtitle:
                    slide["subtitle"] = subtitle
                slides.append(slide)

        if not slides:
            slides = [{"title": "Presentation", "subtitle": "", "bullets": []}]

        return slides

    def _parse_pptx_content(self, content: str, title: str) -> list[dict]:
        """Parse content into slide data. Supports JSON array or plain text fallback."""
        import json

        # Try JSON first
        try:
            data = json.loads(content)
            if isinstance(data, list) and len(data) > 0:
                return data
            if isinstance(data, dict) and "slides" in data:
                return data["slides"]
        except (json.JSONDecodeError, TypeError):
            pass

        # Fallback: parse HTML/text content into slides by splitting on headings
        slides: list[dict] = []
        current_slide: dict | None = None

        # Strip HTML tags for text extraction
        text = re.sub(r"<[^>]+>", "\n", content)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        lines = text.split("\n")

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Detect heading-like lines (short, no period at end, capitalized)
            is_heading = (
                len(line) < 100
                and not line.endswith(".")
                and (line[0].isupper() or line[0].isdigit())
                and not line.startswith("-")
                and not line.startswith("*")
            )

            if is_heading and (not current_slide or len(current_slide.get("bullets", [])) > 0):
                if current_slide:
                    slides.append(current_slide)
                current_slide = {"title": line, "bullets": []}
            elif current_slide:
                # Clean bullet markers
                clean = re.sub(r"^[-*•]\s*", "", line)
                if clean:
                    current_slide["bullets"].append(clean)
            else:
                current_slide = {"title": title, "bullets": [line]}

        if current_slide:
            slides.append(current_slide)

        # If no slides parsed, create a single title slide
        if not slides:
            slides = [{"title": title, "subtitle": "Generated presentation", "bullets": []}]

        return slides

    def _build_pptx_preview_html(self, title: str, content: str, style: str) -> str:
        """Build an HTML preview for a PPTX presentation from HTML section slides."""
        # Check if content uses <section> tags (new rich HTML format)
        if "<section" in content:
            body = content
        else:
            # Legacy JSON format fallback
            import json

            slides_data = self._parse_pptx_content(content, title)
            slides_parts = []
            for i, slide in enumerate(slides_data):
                slide_title = slide.get("title", f"Slide {i + 1}")
                bullets = slide.get("bullets", [])
                subtitle = slide.get("subtitle", "")
                bullets_html = ""
                if bullets:
                    items = "".join(f"<li>{self._escape_html(b)}</li>" for b in bullets)
                    bullets_html = f"<ul>{items}</ul>"
                subtitle_html = f"<p>{self._escape_html(subtitle)}</p>" if subtitle else ""
                slides_parts.append(
                    f"<section><h2>{self._escape_html(slide_title)}</h2>"
                    f"{subtitle_html}{bullets_html}</section>"
                )
            body = "\n".join(slides_parts)

        return self._wrap_presentation_html(title, body)

    def _wrap_presentation_html(self, title: str, body: str) -> str:
        """Wrap slide sections with full presentation CSS styling."""
        css = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    padding: 20px;
    background: #1a1a2e;
    color: #e0e0e0;
    line-height: 1.5;
}
h1.deck-title {
    text-align: center;
    margin-bottom: 24px;
    font-size: 22px;
    color: #fff;
    font-weight: 700;
}
section {
    background: #ffffff;
    border-radius: 16px;
    padding: 36px 32px;
    margin-bottom: 20px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.12);
    color: #1a1a1a;
    position: relative;
    overflow: hidden;
}
section h1 {
    font-size: 28px;
    font-weight: 800;
    margin-bottom: 12px;
    color: #111;
}
section h2 {
    font-size: 22px;
    font-weight: 700;
    margin-bottom: 16px;
    color: #1a1a1a;
    border-bottom: 3px solid #4f46e5;
    padding-bottom: 8px;
    display: inline-block;
}
section h3 {
    font-size: 17px;
    font-weight: 600;
    margin: 14px 0 8px;
    color: #333;
}
section p {
    font-size: 15px;
    margin-bottom: 10px;
    color: #444;
    line-height: 1.6;
}
section ul, section ol {
    padding-left: 20px;
    margin: 10px 0;
}
section li {
    font-size: 15px;
    margin-bottom: 8px;
    color: #333;
    line-height: 1.5;
}
section table {
    width: 100%;
    border-collapse: collapse;
    margin: 14px 0;
    font-size: 14px;
}
section th {
    background: #4f46e5;
    color: white;
    padding: 10px 14px;
    text-align: left;
    font-weight: 600;
}
section td {
    padding: 10px 14px;
    border-bottom: 1px solid #eee;
    color: #333;
}
section tr:nth-child(even) td { background: #f8f8ff; }
section blockquote {
    border-left: 4px solid #4f46e5;
    padding: 12px 16px;
    margin: 14px 0;
    background: #f5f3ff;
    border-radius: 0 8px 8px 0;
    font-style: italic;
    color: #4a4a6a;
}
section blockquote cite {
    display: block;
    margin-top: 8px;
    font-style: normal;
    font-size: 13px;
    color: #666;
    font-weight: 600;
}
section svg {
    display: block;
    margin: 16px auto;
    max-width: 100%;
}
.columns {
    display: flex;
    gap: 16px;
    margin: 14px 0;
}
.columns > div {
    flex: 1;
    background: #f8fafc;
    padding: 16px;
    border-radius: 10px;
    border: 1px solid #e2e8f0;
}
.columns > div h3 { margin-top: 0; }
.stat {
    display: inline-flex;
    flex-direction: column;
    align-items: center;
    background: #f0f0ff;
    padding: 16px 24px;
    border-radius: 12px;
    margin: 8px 12px 8px 0;
}
.stat .number {
    font-size: 32px;
    font-weight: 800;
    color: #4f46e5;
    line-height: 1;
}
.stat .label {
    font-size: 12px;
    color: #666;
    margin-top: 4px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.highlight {
    background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%);
    color: white;
    padding: 18px 22px;
    border-radius: 12px;
    margin: 14px 0;
}
.highlight h3, .highlight p, .highlight li { color: white; }
.highlight ul { padding-left: 20px; }
.timeline { margin: 14px 0; }
.timeline .event {
    padding: 12px 0 12px 20px;
    border-left: 3px solid #4f46e5;
    margin-bottom: 4px;
    position: relative;
}
.timeline .event::before {
    content: '';
    position: absolute;
    left: -7px;
    top: 16px;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: #4f46e5;
}
.timeline .event b { color: #4f46e5; font-size: 14px; }
.timeline .event p { margin: 4px 0 0; font-size: 14px; color: #555; }
"""
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{self._escape_html(title)}</title>
<style>{css}</style>
</head>
<body>
<h1 class="deck-title">{self._escape_html(title)}</h1>
{body}
</body>
</html>"""

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
