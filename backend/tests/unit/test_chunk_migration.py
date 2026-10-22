from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_upgrade_preserves_legacy_index_identifiers_and_matching_records():
    path = Path(__file__).resolve().parents[2] / "alembic/versions/20261018_0003_chunk_retrieval.py"
    spec = importlib.util.spec_from_file_location("chunk_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE achievements (id VARCHAR(36) PRIMARY KEY, qdrant_indexed BOOLEAN)"
            )
        )
        conn.execute(
            sa.text(
                "CREATE TABLE document_chunks (id VARCHAR(36) PRIMARY KEY, "
                "qdrant_point_id VARCHAR(64))"
            )
        )
        conn.execute(sa.text("CREATE TABLE match_items (id VARCHAR(36) PRIMARY KEY)"))
        conn.execute(sa.text("INSERT INTO achievements VALUES ('a', 1)"))
        conn.execute(sa.text("INSERT INTO document_chunks VALUES ('d', 'legacy-point')"))
        conn.execute(sa.text("INSERT INTO match_items VALUES ('m')"))
        module.op = Operations(MigrationContext.configure(conn))
        module.upgrade()
        module.upgrade()
        assert conn.scalar(sa.text("SELECT search_indexed FROM achievements WHERE id='a'")) == 1
        assert (
            conn.scalar(sa.text("SELECT search_document_id FROM document_chunks WHERE id='d'"))
            == "legacy-point"
        )
        row = conn.execute(
            sa.text("SELECT eligibility, score_kind, constraint_checks FROM match_items")
        ).one()
        assert tuple(row) == ("unknown", "retrieval_relevance", "[]")
        assert "achievement_chunks" in sa.inspect(conn).get_table_names()
    engine.dispose()
