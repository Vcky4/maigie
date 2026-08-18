"""Ask the live model for the diagram that truncated, and check it comes back whole.

The reported failure was a mermaid body cut off mid-label — `C1["ID 101 | Plan: Basic` — which the browser
could not draw. The mermaid itself was legal: every construct in it was parsed against mermaid and passed. It
failed only because it stopped early, and it reached the browser looking valid because the JSON repair closed
the dangling string.

Two things are being checked here, both against the real provider rather than a fake:

1. That 8192 output tokens is enough for this diagram. The payload is small, but the configured model draws
   its reasoning from the same allowance, which is why 1200 and then 2048 both truncated.
2. That the completeness guard passes a genuine diagram. A heuristic that rejected real output would be worse
   than the bug it was written for, so the balance counts are printed rather than assumed.

Run twice, because the failure was intermittent.
"""

from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.WARNING)

_HINT = "messy churn data being cleaned in phase 2, showing duplicates and nulls"


async def main() -> None:
    # The provider is resolved per learner from `LearningProfile.preferred_llm_provider`, so this needs a
    # database even though nothing else here does. Without it every attempt failed with "Database not
    # initialized" — which the fallback correctly turned into "the model returned an empty diagram", so the
    # first run of this script proved the error handling and nothing about the token budget.
    from src.shared.database import connect_db

    await connect_db()

    from src.domains.study_voice import diagram

    topic = type("T", (), {"id": "t", "title": "The Data Analytics Lifecycle"})()
    course = type("C", (), {"id": "c", "title": "Data Analytics Foundations", "user_id": "u"})()

    async def own(_topic_id, _user_id):
        return topic, None, course

    async def no_notes(_user_id, where=None, take=None):
        return [], 0

    diagram.check_topic_ownership = own
    diagram.personal_learning_repo.list_notes = no_notes

    for attempt in (1, 2, 3):
        try:
            result = await diagram.generate_for_topic("u", topic_id="t", hint=_HINT)
        except Exception as error:
            print(f"attempt {attempt}: {type(error).__name__}: {error}")
            continue

        body = result["mermaid"]
        quotes = body.count('"')
        subgraphs = body.count("subgraph ")
        ends = len([line for line in body.splitlines() if line.strip() == "end"])
        print(
            f"attempt {attempt}: OK  {len(body)} chars, {len(body.splitlines())} lines | "
            f"quotes={quotes} even={quotes % 2 == 0} | subgraph={subgraphs} end={ends} | "
            f"brackets={body.count('[')}/{body.count(']')}"
        )
        print(f"  last line: {(body.splitlines()[-1][:90] if body else '(none)')}")


asyncio.run(main())
