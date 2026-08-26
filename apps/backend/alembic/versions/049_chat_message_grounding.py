"""Add ChatMessage.citations, .truncated and .askMode, so an answer can say how it was produced.

Three facts about a generated answer exist at generation time and were being thrown away. Each is a
case of the same rule: **an answer must be distinguishable from a different answer that happens to look
the same on screen.**

`citations` — `generate_grounded_content` already returns `GroundingSource` objects and the chat
pipeline discards them. Without them a grounded answer and an ungrounded one render identically, and the
learner cannot check anything. **Nullable with no default, deliberately.** `NULL` means "grounding was
not attempted for this turn"; `[]` means "search ran and found nothing to cite". Defaulting to `[]`
would collapse those two into one and make every historical row look like a failed search. This is the
absent-as-zero rule the plan's §1 states, applied to a column.

`truncated` — the Gemini layer already reports it. An answer cut off at the token limit currently
persists as a complete short answer, so nothing downstream can offer to continue it and nobody
auditing the table can tell the difference. Defaulted to `false` rather than nullable because every
existing row *was* delivered as complete, so `false` is the true value for them, not a stand-in.

`askMode` — which surface and transport produced the turn. Ask Maigie was unmetered for its whole life
because the one live endpoint bypassed cost tracking entirely, and nothing in the data would have shown
which surface was responsible. Recording it per row is what makes a recurrence visible per surface
instead of only in aggregate. Nullable, because rows written before this migration genuinely do not know.

**Rejected: a separate `ChatMessageGrounding` table.** Citations are one-to-one with a message, are read
on exactly the same query as the message, and are never queried independently. A join table would add a
join to the hot path of the chat thread to normalise data with no independent lifecycle.

**Rejected: folding all three into `componentData`.** That column is the client's rendering payload and
is passed through to the frontend as-is. Putting provenance in it would mean the client could change the
audit record, and a cost investigation would have to parse presentation JSON.

**No backfill.** `citations` and `askMode` stay `NULL` on existing rows, which is accurate: those turns
were produced by a path that recorded neither. `truncated` backfills to `false` via the server default,
which is accurate for the same reason.

Revision ID: 049_chat_msg_grounding
Revises: 048_plan_item_block_link
Create Date: 2026-08-26

The revision id is 22 characters. `alembic_version.version_num` is `varchar(32)` in this database — see
migration 046, whose first attempt was 33 and failed on the version bump after the DDL had applied. The
plan called this migration `049_add_chat_message_grounding`, which is 30 and would have fitted with two
characters to spare; shortened rather than run that close to a limit that fails *after* the schema
change has been applied.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "049_chat_msg_grounding"
down_revision = "048_plan_item_block_link"
branch_labels = None
depends_on = None

TABLE = "ChatMessage"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("citations", postgresql.JSONB(), nullable=True))
    op.add_column(
        TABLE,
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(TABLE, sa.Column("askMode", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column(TABLE, "askMode")
    op.drop_column(TABLE, "truncated")
    op.drop_column(TABLE, "citations")
