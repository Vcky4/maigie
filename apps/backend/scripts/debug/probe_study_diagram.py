"""Ask the live provider for a study diagram, to find out why one came back empty.

The diagram route answered a `500` because `generate_content_json` re-raised on an **empty** model reply. The
error handling is fixed, but a polite failure is still a failure: this establishes whether the provider can
produce the payload the prompt asks for at all, and which provider is answering.

Prints key presence by length only, never a value.
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.WARNING)

_PROMPT = (
    "You help visualize ideas during a live voice study session.\n"
    "Return ONLY a single JSON object with these keys:\n"
    '- "mermaid": a valid Mermaid diagram body WITHOUT backtick fences, at most 30 lines. Start with '
    "flowchart TD, graph LR, sequenceDiagram or mindmap. Any node label containing parentheses, "
    'brackets, colons or slashes MUST be double-quoted, e.g. A["V (vectors)"] and never '
    'A[V (vectors)]. Use "" if a diagram is not appropriate.\n'
    '- "display_math": LaTeX for ONE display equation with no $ or $$ delimiters, or "".\n'
    '- "caption": one short line, or "".\n'
    "At least one of mermaid or display_math must be non-empty.\n\n"
    "Course: Data Analytics Foundations\n"
    "Topic: The Data Analytics Lifecycle\n\n"
    "The learner's notes:\n(no notes yet)\n\n"
    "Recent voice transcript, which may be fragmented:\n(none)\n\n"
    "What to illustrate: the six phases of the lifecycle in order\n"
)


async def main() -> None:
    for key in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "LLM_PROVIDER"):
        value = os.environ.get(key)
        if key == "LLM_PROVIDER":
            print(f"{key:<20} {value or 'unset'}")
        else:
            print(f"{key:<20} {'set (len ' + str(len(value)) + ')' if value else 'MISSING'}")

    from src.domains.personal_learning.services.llm_resilient import (
        generate_content,
        generate_content_json,
    )

    print("\n--- raw text reply ---")
    try:
        raw = await generate_content(_PROMPT, max_tokens=2048, temperature=0.4, fallback=None)
        print(f"length: {len(raw or '')}")
        print(repr((raw or "")[:400]))
    except Exception as error:
        print(f"RAISED {type(error).__name__}: {error}")

    print("\n--- parsed, with a fallback ---")
    try:
        parsed = await generate_content_json(_PROMPT, max_tokens=2048, temperature=0.4, fallback={})
        print(f"type: {type(parsed).__name__}")
        if isinstance(parsed, dict):
            print(f"keys: {sorted(parsed)}")
            print(f"mermaid ({len(str(parsed.get('mermaid') or ''))} chars):")
            print(str(parsed.get("mermaid") or "")[:400])
            print(f"display_math: {str(parsed.get('display_math') or '')[:120]!r}")
            print(f"caption: {str(parsed.get('caption') or '')[:120]!r}")
    except Exception as error:
        print(f"RAISED {type(error).__name__}: {error}")


asyncio.run(main())
