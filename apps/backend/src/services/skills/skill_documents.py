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
                "in the user's academic or professional context."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "enum": ["pdf", "docx"],
                        "description": "Document format. Use 'docx' for academic reports, essays, and editable documents. Use 'pdf' for reference materials and study guides.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Document title. Should be descriptive and appropriate for the document type.",
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "The document body as HTML markup. Produce high-quality, comprehensive content "
                            "matching the user's requested document type and academic level. "
                            "HTML tags to use: "
                            "<h1> for document/chapter titles; <h2> for major sections; <h3> for subsections; <h4> for sub-subsections; "
                            "<p> for paragraphs; <ul>/<ol> with <li> for lists; "
                            "<table> with <thead>, <tbody>, <tr>, <th>, <td> for data tables; "
                            "<b>/<i> for emphasis; <code> for inline code; <pre> for code blocks; "
                            "<hr> for page/section breaks. "
                            "RULES: "
                            "- Do NOT include <html>, <head>, <body>, or <style> tags. "
                            "- Use plain ASCII only (no smart quotes, em-dashes, or special unicode). "
                            "- For academic reports: start with metadata (author, department, date, institution) in a structured block, "
                            "  then abstract, table of contents, chapters with proper decimal numbering (1.1, 1.2, 2.1...). "
                            "- For tables: always include <thead> with <th> headers and <tbody> with <td> data cells. "
                            "- Use <hr> between major chapters/sections to indicate page breaks. "
                            "- Ensure references use proper academic citation format. "
                            "- Content must be thorough, well-argued, and ready for submission without editing."
                        ),
                    },
                    "style": {
                        "type": "string",
                        "enum": ["academic", "report", "minimal"],
                        "description": (
                            "Document style. 'academic' for project reports, theses, research papers (adds formal title page, "
                            "page breaks between chapters, Times New Roman style). "
                            "'report' for professional/business reports (clean, modern formatting). "
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
