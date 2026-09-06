"""Name conversations that were created before anything titled them.

Plan §4.5.10. The socket has always titled a conversation from its first message, but three groups of rows
never went through that writer: everything created before the intelligence router was mounted, everything
created through `conversation_service.create_conversation`, and the space-room sessions the
`learning_spaces` domain creates. Those all read `"New Chat"`, and Phase 6's history panel lists
conversations so a learner can find one again — twenty rows reading "New Chat" is not a list you can find
anything in.

**Truncated-first-message rather than a generated summary, and this is the decision the plan asked for.**
A summary reads better and costs a model call per conversation, on rows nobody has asked to see. The first
message is what the learner actually typed, so it is recognisable to them even when it is clumsy — which
is the whole job of a history row. If product later wants summaries, this script is what proves how many
rows it would cost.

**Only rows nothing has named.** A conversation the learner renamed, or one already titled by its first
message, is left alone. The default is matched exactly rather than by emptiness, because
`ask_service.session_needs_a_title` is the live gate and the two must agree — a row this script skips and
that gate would also skip is a row that stays "New Chat" forever, which is the bug.

Dry by default. Pass ``--apply`` to write.

    python scripts/backfill_conversation_titles.py
    python scripts/backfill_conversation_titles.py --apply
"""

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.domains.intelligence.conversation import ask_service  # noqa: E402
from src.domains.intelligence.db_models import ChatMessage, ChatSession  # noqa: E402
from src.shared.database.session import (  # noqa: E402
    connect_db,
    disconnect_db,
    get_session_factory,
)


async def backfill(*, apply: bool) -> None:
    await connect_db()
    factory = get_session_factory()

    async with factory() as session:
        untitled = (
            (
                await session.execute(
                    select(ChatSession).where(
                        ChatSession.title.in_([ask_service.NEW_CONVERSATION_TITLE, ""])
                        | ChatSession.title.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )

    print(f"{len(untitled)} conversation(s) with no name of their own")

    named = skipped = 0
    for conversation in untitled:
        async with factory() as session:
            # The first thing the learner said, which is what the live gate names a conversation after.
            # Review rows are excluded for the same reason the gate excludes them: a review thread is
            # addressed by its review item and is not listed as a conversation.
            first = (
                (
                    await session.execute(
                        select(ChatMessage)
                        .where(
                            ChatMessage.session_id == conversation.id,
                            ChatMessage.role == "USER",
                            ChatMessage.review_item_id.is_(None),
                        )
                        .order_by(ChatMessage.created_at.asc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )

        title = ask_service.derive_session_title(first.content if first else "")
        if not title:
            # An empty conversation, or one whose only messages are a review. Left as it is: a blank title
            # is worse than the default, which at least says "new".
            skipped += 1
            continue

        print(f"  {conversation.id}  ->  {title!r}")
        named += 1
        if apply:
            async with factory() as session:
                row = await session.get(ChatSession, conversation.id)
                if row is not None:
                    row.title = title
                    await session.commit()

    print(f"\n{named} nameable, {skipped} left alone (no first message to name them after)")
    if not apply:
        print("Dry run. Pass --apply to write.")

    await disconnect_db()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write the titles.")
    asyncio.run(backfill(apply=parser.parse_args().apply))
