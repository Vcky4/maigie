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
                "Structure the content well with headings, bullet points, and sections. "
                "The content should be comprehensive and well-formatted in markdown."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "enum": ["pdf", "docx"],
                        "description": "Document format to generate. Use 'pdf' for PDF files and 'docx' for Word documents.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Document title. Should be descriptive and concise.",
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "The document content in markdown format. Include proper headings (# ## ###), "
                            "bullet points (- or *), numbered lists (1. 2. 3.), code blocks (```), "
                            "and paragraphs. Make it comprehensive and well-structured."
                        ),
                    },
                    "style": {
                        "type": "string",
                        "enum": ["academic", "report", "minimal"],
                        "description": (
                            "Document style. 'academic' for research papers and essays, "
                            "'report' for project reports, 'minimal' for clean simple documents. "
                            "Default: 'academic'."
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
