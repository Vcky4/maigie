"""
Document Generation Skill — generate downloadable PDF and DOCX documents from chat content.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.services.skills.types import Skill, SkillCategory, ToolDefinition

if TYPE_CHECKING:
    from src.services.skills.registry import SkillRegistry


SKILL = Skill(
    name="document_generation",
    display_name="Document Generation",
    description="Generate downloadable documents (PDF, DOCX) from chat content for projects, research, and study materials.",
    category=SkillCategory.ACTION,
    triggers=[
        "document",
        "pdf",
        "docx",
        "word",
        "pptx",
        "presentation",
        "slides",
        "powerpoint",
        "export",
        "download",
        "generate document",
        "create document",
        "make a pdf",
        "save as",
        "file",
        "report",
        "paper",
    ],
    always_active=True,
    tools=[
        ToolDefinition(
            name="generate_document",
            description=(
                "Generate a downloadable document (PDF or DOCX) from content. "
                "Use this when the user asks to create a document, export to PDF, "
                "generate a report, make a Word file, or save content as a downloadable file. "
                "IMPORTANT: Adapt the document style, depth, and formatting to match what the user needs. "
                "For academic project reports: include title page info, chapters, proper numbering, "
                "methodology, findings, references in academic citation format. "
                "For study notes: use concise headings, bullet points, key definitions. "
                "For general documents: use clean professional formatting. "
                "Always produce publication-ready content that matches the standard expected "
                "in the user's academic or professional context. "
                "The content MUST be substantial and thorough — at minimum 2000 words for reports and essays. "
                "Do not produce skeleton or outline-only content. Write full paragraphs, complete arguments, "
                "and proper analysis as if you were writing the actual paper. "
                "CRITICAL RESPONSE RULES after calling this tool: "
                "1. Keep your chat response to ONE short sentence like 'Your document is ready!' and offer further help."
                "2. Do NOT mention the download card, say 'above', 'below', or reference card placement. "
                "3. Do NOT include any download link, URL, or file path. "
                "4. The download card appears automatically — just confirm and stop."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "enum": ["pdf", "docx", "pptx"],
                        "description": "Document format. Use 'docx' for academic reports, essays, and editable documents. Use 'pdf' for reference materials and study guides. Use 'pptx' for presentations and slides.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Document title. Should be descriptive and appropriate for the document type.",
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "The document content. Format depends on the chosen format: "
                            "FOR PDF/DOCX: Full document body in HTML format. Use "
                            "<h1>, <h2>, <h3> for headings; <p> for paragraphs; <ul>/<ol> with <li> for lists; "
                            "<table> with <thead>, <tbody>, <tr>, <th>, <td> for tables; "
                            "<b>/<i> for emphasis; <pre> for code blocks; <blockquote> for quotes; <hr> for breaks. "
                            "Do NOT include <html>, <head>, <body>, or <style> tags. "
                            "FOR PPTX: A JSON array of slide objects. Each slide has: "
                            '{"title": "Slide Title", "bullets": ["Point 1", "Point 2", ...], "notes": "Optional speaker notes"}. '
                            "First slide should be the title slide (include a 'subtitle' field). "
                            "Aim for 8-15 slides with 3-6 bullet points per slide. Keep bullets concise (under 15 words). "
                            "CONTENT REQUIREMENTS: "
                            "- For PDF/DOCX academic reports/essays: MINIMUM 2000-3000 words, full paragraphs, "
                            "  complete arguments, introduction, body, conclusion, and references. "
                            "- For PDF/DOCX book reviews: 1500+ words with analysis and critical evaluation. "
                            "- For PPTX: 8-15 well-structured slides covering the topic comprehensively. "
                            "- NEVER produce skeleton/outline-only content. Write full text (PDF/DOCX) or comprehensive slides (PPTX). "
                            "- Use plain ASCII only (no smart quotes, em-dashes, or special unicode)."
                        ),
                    },
                    "style": {
                        "type": "string",
                        "enum": ["academic", "report", "minimal"],
                        "description": (
                            "Document style. 'academic' for project reports, theses, research papers. "
                            "'report' for professional/business reports. "
                            "'minimal' for study notes, reference sheets, quick exports."
                        ),
                    },
                },
                "required": ["format", "title", "content"],
            },
        ),
    ],
)


def register(registry: SkillRegistry) -> None:
    """Register the document generation skill."""
    from src.services.skills.handlers import handle_generate_document

    registry.register_skill(
        SKILL,
        handlers={
            "generate_document": handle_generate_document,
        },
    )
