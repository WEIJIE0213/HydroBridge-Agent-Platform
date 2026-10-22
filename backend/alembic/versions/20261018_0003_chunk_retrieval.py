"""Add versioned evidence chunks and database-neutral indexing metadata."""

import sqlalchemy as sa

from alembic import op
from app.db.models.knowledge import AchievementChunk

revision = "20261018_0003"
down_revision = "20260817_0002"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    for table, old, new, type_ in (
        ("achievements", "qdrant_indexed", "search_indexed", sa.Boolean()),
        ("document_chunks", "qdrant_point_id", "search_document_id", sa.String(64)),
    ):
        columns = {c["name"] for c in sa.inspect(bind).get_columns(table)}
        if old in columns and new not in columns:
            op.alter_column(table, old, new_column_name=new, existing_type=type_)
    AchievementChunk.__table__.create(bind, checkfirst=True)
    columns = {c["name"] for c in sa.inspect(bind).get_columns("match_items")}
    for name, type_, default in (
        ("eligibility", sa.String(20), "unknown"),
        ("score_kind", sa.String(40), "retrieval_relevance"),
        ("constraint_checks", sa.JSON(), "[]"),
    ):
        if name not in columns:
            op.add_column(
                "match_items", sa.Column(name, type_, server_default=default, nullable=False)
            )


def downgrade():
    raise RuntimeError("Forward-only migration: export evidence before changing storage")
