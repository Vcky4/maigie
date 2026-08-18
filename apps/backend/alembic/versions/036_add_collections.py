"""Add Collection and CollectionItem tables for cross-library groupings.

The Learn dashboard's `collections` field has returned `[]` since the programme started. This
migration creates the persistence that fills it.

A Collection is a named group of learning artifacts (notes, decks, saved resources, documents)
belonging to one learner. It can be auto-seeded from a tag or created manually. `sourceTag` records
where it came from, and `deletedAt` is a soft delete so the seeding logic knows not to recreate
something the learner deliberately removed.

CollectionItem is the membership join. No FK from `entityId` to the artifact tables — the four types
live in separate tables and a polymorphic FK is not supported. Dangling references (a deleted note
still listed) are filtered at read time by joining the source table and excluding missing rows.

Revision ID: 036_add_collections
Revises: 035_add_topic_illustration
Create Date: 2026-08-18
"""

import sqlalchemy as sa

from alembic import op

revision = "036_add_collections"
down_revision = "035_add_topic_illustration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "Collection",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("userId", sa.String(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sourceTag", sa.String(100), nullable=True),
        sa.Column("deletedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # The dashboard reads non-deleted collections for one user.
    op.create_index("Collection_userId_deletedAt_idx", "Collection", ["userId", "deletedAt"])
    # Prevent duplicate live collections for the same source tag.
    op.create_index(
        "Collection_userId_sourceTag_live_uq",
        "Collection",
        ["userId", "sourceTag"],
        unique=True,
        postgresql_where=sa.text('"deletedAt" IS NULL AND "sourceTag" IS NOT NULL'),
    )

    op.create_table(
        "CollectionItem",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("collectionId", sa.String(), nullable=False),
        sa.Column("entityType", sa.String(20), nullable=False),
        sa.Column("entityId", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column(
            "addedAt", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.ForeignKeyConstraint(["collectionId"], ["Collection.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # One artifact can appear in a collection at most once.
    op.create_index(
        "CollectionItem_collection_entity_uq",
        "CollectionItem",
        ["collectionId", "entityType", "entityId"],
        unique=True,
    )
    # Ordered reads within a collection.
    op.create_index(
        "CollectionItem_collectionId_position_idx",
        "CollectionItem",
        ["collectionId", "position"],
    )


def downgrade() -> None:
    op.drop_index("CollectionItem_collectionId_position_idx", table_name="CollectionItem")
    op.drop_index("CollectionItem_collection_entity_uq", table_name="CollectionItem")
    op.drop_table("CollectionItem")
    op.drop_index("Collection_userId_sourceTag_live_uq", table_name="Collection")
    op.drop_index("Collection_userId_deletedAt_idx", table_name="Collection")
    op.drop_table("Collection")
