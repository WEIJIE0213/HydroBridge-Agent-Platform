"""SQLAlchemy model registry."""


def import_all_models() -> None:
    # Imports are intentionally local so Alembic and tests can register all tables.
    from app.db.models import domain, evaluation, identity, knowledge, runtime  # noqa: F401


import_all_models()
