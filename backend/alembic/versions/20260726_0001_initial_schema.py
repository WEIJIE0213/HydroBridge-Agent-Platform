"""Create the complete HydroBridge domain schema.

Revision ID: 20260726_0001
Revises:
Create Date: 2026-07-26 20:00:00
"""

from collections.abc import Sequence

from alembic import op

from app.db.base import Base
from app.db.models import import_all_models

revision: str = "20260726_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    import_all_models()
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    import_all_models()
    Base.metadata.drop_all(bind=op.get_bind())
