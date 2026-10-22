"""Complete evaluation, requirement refinement and matching workflows.

Revision ID: 20260817_0002
Revises: 20260726_0001
Create Date: 2026-08-17 02:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0002"
down_revision: str | Sequence[str] | None = "20260726_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def _add_json_column(table: str, column: str, empty_value: str) -> None:
    op.add_column(table, sa.Column(column, sa.JSON(), nullable=True))
    op.execute(sa.text(f"UPDATE {table} SET {column} = '{empty_value}' WHERE {column} IS NULL"))
    op.alter_column(table, column, existing_type=sa.JSON(), nullable=False)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    requirement_columns = _columns("requirements")
    for name, empty in (
        ("objectives", "[]"),
        ("known_conditions", "{}"),
        ("search_terms", "[]"),
    ):
        if name not in requirement_columns:
            _add_json_column("requirements", name, empty)

    evaluation_columns = _columns("achievement_evaluations")
    if "evaluation_version" not in evaluation_columns:
        op.add_column(
            "achievement_evaluations",
            sa.Column("evaluation_version", sa.Integer(), server_default="1", nullable=False),
        )
    if "status" not in evaluation_columns:
        op.add_column(
            "achievement_evaluations",
            sa.Column("status", sa.String(length=30), server_default="queued", nullable=False),
        )
        op.create_index(
            "ix_achievement_evaluations_status",
            "achievement_evaluations",
            ["status"],
        )
    for name, empty in (
        ("dimension_details", "{}"),
        ("strengths", "[]"),
        ("missing_information", "[]"),
        ("recommendations", "[]"),
        ("rubric_snapshot", "{}"),
    ):
        if name not in evaluation_columns:
            _add_json_column("achievement_evaluations", name, empty)
    if "level" not in evaluation_columns:
        op.add_column(
            "achievement_evaluations",
            sa.Column("level", sa.String(length=50), server_default="", nullable=False),
        )
    if "verification_status" not in evaluation_columns:
        op.add_column(
            "achievement_evaluations",
            sa.Column(
                "verification_status",
                sa.String(length=30),
                server_default="pending",
                nullable=False,
            ),
        )

    if "requirement_refinements" not in tables:
        op.create_table(
            "requirement_refinements",
            sa.Column("requirement_id", sa.String(length=36), nullable=False),
            sa.Column("agent_run_id", sa.String(length=36), nullable=True),
            sa.Column("refinement_version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("profile", sa.JSON(), nullable=False),
            sa.Column("completeness_score", sa.Float(), nullable=False),
            sa.Column("confirmed", sa.Boolean(), nullable=False),
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["agent_run_id"],
                ["agent_runs.id"],
                name="fk_requirement_refinements_agent_run_id_agent_runs",
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["requirement_id"],
                ["requirements.id"],
                name="fk_requirement_refinements_requirement_id_requirements",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name="pk_requirement_refinements"),
        )
        op.create_index(
            "ix_requirement_refinements_requirement_created",
            "requirement_refinements",
            ["requirement_id", "created_at"],
        )
        op.create_index(
            "ix_requirement_refinements_status",
            "requirement_refinements",
            ["status"],
        )

    match_columns = _columns("matches")
    if "candidate_count" not in match_columns:
        op.add_column(
            "matches",
            sa.Column("candidate_count", sa.Integer(), server_default="0", nullable=False),
        )

    item_columns = _columns("match_items")
    for name, empty in (
        ("dimension_scores", "{}"),
        ("hard_constraint_violations", "[]"),
    ):
        if name not in item_columns:
            _add_json_column("match_items", name, empty)
    if "recommendation" not in item_columns:
        op.add_column(
            "match_items",
            sa.Column("recommendation", sa.Text(), nullable=True),
        )
        op.execute(sa.text("UPDATE match_items SET recommendation = '' WHERE recommendation IS NULL"))
        op.alter_column(
            "match_items",
            "recommendation",
            existing_type=sa.Text(),
            nullable=False,
        )
    if "decision_status" not in item_columns:
        op.add_column(
            "match_items",
            sa.Column(
                "decision_status",
                sa.String(length=30),
                server_default="pending",
                nullable=False,
            ),
        )
        op.create_index("ix_match_items_decision_status", "match_items", ["decision_status"])
    if "decided_by" not in item_columns:
        op.add_column("match_items", sa.Column("decided_by", sa.String(length=36)))
        op.create_foreign_key(
            "fk_match_items_decided_by_users",
            "match_items",
            "users",
            ["decided_by"],
            ["id"],
            ondelete="SET NULL",
        )
    if "decided_at" not in item_columns:
        op.add_column("match_items", sa.Column("decided_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    # This migration contains user decisions and evaluation history. Downgrade is
    # intentionally conservative: the application supports forward-only schema upgrades.
    pass
