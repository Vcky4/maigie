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
                            "FOR PDF/DOCX: Full document body in HTML. Use "
                            "<h1>, <h2>, <h3> for headings; <p> for paragraphs; <ul>/<ol> with <li> for lists; "
                            "<table> with <thead>, <tbody>, <tr>, <th>, <td> for tables; "
                            "<b>/<i> for emphasis; <pre> for code blocks; <blockquote> for quotes; <hr> for breaks. "
                            "Do NOT include <html>, <head>, <body>, or <style> tags. "
                            "FOR PPTX: Produce rich HTML slides using <section> tags. Each <section> is one slide. "
                            "Structure: <section><h2>Slide Title</h2>...content...</section>. "
                            "Make slides VISUALLY RICH using these HTML patterns: "
                            "- Use <ul> and <li> for bullet points "
                            "- Use <table> for comparison data and structured information "
                            "- Use <div class='columns'><div>Left</div><div>Right</div></div> for two-column layouts "
                            "- Use <div class='stat'><span class='number'>85%</span><span class='label'>Success Rate</span></div> for key statistics "
                            "- Use <blockquote> for important quotes with <cite> for attribution "
                            "- Use <svg> for simple inline diagrams: flowcharts (boxes + arrows), cycles, hierarchies. "
                            "  SVG example: <svg width='400' height='120' viewBox='0 0 400 120'>"
                            "<rect x='10' y='40' width='100' height='40' rx='8' fill='#4f46e5' />"
                            "<text x='60' y='65' text-anchor='middle' fill='white' font-size='12'>Step 1</text>"
                            "<line x1='110' y1='60' x2='150' y2='60' stroke='#666' stroke-width='2' marker-end='url(#arrow)'/>"
                            "<rect x='150' y='40' width='100' height='40' rx='8' fill='#7c3aed' />"
                            "<text x='200' y='65' text-anchor='middle' fill='white' font-size='12'>Step 2</text>"
                            "</svg> "
                            "- Use <div class='highlight'> for key takeaway boxes "
                            "- Use <div class='timeline'><div class='event'><b>Date</b><p>Description</p></div></div> for timelines "
                            "PRESENTATION REQUIREMENTS: "
                            "- 10-15 slides minimum. First <section> is title slide with <h1> and <p> subtitle. "
                            "- Include at least 2-3 SVG diagrams/flowcharts across the presentation. "
                            "- Include at least 1 table or comparison layout. "
                            "- Use stat callouts for key numbers/percentages. "
                            "- Make content educational, detailed, and visually varied — NOT just bullet lists. "
                            "- Each slide should have a distinct visual treatment (mix bullets, diagrams, tables, stats, quotes). "
                            "CONTENT REQUIREMENTS (all formats): "
                            "- For PDF/DOCX academic reports/essays: MINIMUM 2000-3000 words with full paragraphs. "
                            "- For PDF/DOCX book reviews: 1500+ words with analysis. "
                            "- For PPTX: 10-15 visually rich slides. "
                            "- NEVER produce skeleton/outline-only content. "
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
