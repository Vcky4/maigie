"""Keep the diagrams and equations generated while studying a topic.

Two paths in the product generated visuals and neither kept them.

The voice tutor calls `study_show_visual` when a picture would help, and the learner can ask for one
directly through `POST /gemini-live/study/diagram`, which pre-checks and then charges 80 credits. Both
delivered `{mermaid, display_math, caption}` to the browser, where the client parsed it into typed content
blocks and put it in a zustand map capped at 48 entries — **which nothing in the application read.** There
was also no mermaid or KaTeX package installed anywhere in the client, so even a consumer would have had
nothing to draw with.

The result was a feature that charged for its output and produced none. Worse, the tutor was told
otherwise: the server-composed brief in `study_voice/context.py` instructs the model to "call the
study_show_visual tool so it appears on their screen", so the model announced a diagram and the learner
looked at an empty overlay. Two button tooltips went further and named a "topic page Illustrations tab"
that has never existed in the codebase.

Rendering the blocks was the first half of the fix. This is the second: a diagram a learner paid for and
learned from should survive a reload, and a lesson they come back to should still show what it showed them.
Client memory cannot do either.

## Why columns rather than the client's block array

The web models these as `MessageContentBlock[]` and storing that shape verbatim was the obvious move. It is
rejected because the server would then hold a blob it cannot reason about: it could not tell a diagram from
an image, could not require that a row contain something drawable, and could not refuse an `image` block
pointing at an arbitrary URL. Both producers emit exactly three values, so there are three columns, and the
client composes blocks from them the way it already does for the REST response.

The check constraint is the part worth keeping: a row with neither a diagram nor an equation renders as an
empty panel, which reads as a broken feature rather than an absent one. `generate_for_topic` already raises
rather than returning a blank diagram for the same reason, and this puts that rule where it cannot be
bypassed by a second writer.

## Why `userId`

Following `TopicCheckAttempt`. Classrooms assign courses to their members, so several learners genuinely
study one topic, and a visual generated inside one learner's conversation was shaped by what that learner
was struggling with. Without the column, opening a lesson would show you diagrams drawn for somebody else's
confusion.

## No `updatedAt`

An illustration is an event: generated once, never edited. Deletion is offered, because an unhelpful diagram
is clutter on a lesson the learner returns to, and deleting is not editing.

The revision id is 26 characters; `alembic_version.version_num` is `varchar(32)`, and an over-long id fails
*after* the DDL has run and rolls the whole migration back.

Revision ID: 035_add_topic_illustration
Revises: 034_add_document_type
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "035_add_topic_illustration"
down_revision = "034_add_document_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "TopicIllustration",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("topicId", sa.String(), nullable=False),
        sa.Column("userId", sa.String(), nullable=False),
        sa.Column("mermaid", sa.Text(), nullable=True),
        sa.Column("displayMath", sa.Text(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("source", sa.String(), nullable=False, server_default="tutor"),
        sa.Column("createdAt", sa.DateTime(timezone=True), nullable=False),
        # CASCADE on both, and for the same reason each time: an illustration is meaningless without the
        # topic it illustrates, and belongs to nobody once the learner is gone. This is deliberately not
        # the `SET NULL` that notes and resources get on course delete — a note survives its course as a
        # piece of the learner's own writing, whereas a diagram of a deleted lesson illustrates nothing.
        sa.ForeignKeyConstraint(["topicId"], ["Topic.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "(mermaid IS NOT NULL AND mermaid <> '') OR"
            ' ("displayMath" IS NOT NULL AND "displayMath" <> \'\')',
            name="TopicIllustration_has_content",
        ),
    )
    # One index, matching the only query: this learner's illustrations for this topic, newest first.
    op.create_index(
        "TopicIllustration_topicId_userId_createdAt_idx",
        "TopicIllustration",
        ["topicId", "userId", "createdAt"],
    )


def downgrade() -> None:
    op.drop_index("TopicIllustration_topicId_userId_createdAt_idx", table_name="TopicIllustration")
    op.drop_table("TopicIllustration")
