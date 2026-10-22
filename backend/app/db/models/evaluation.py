from __future__ import annotations

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class EvalDataset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "eval_datasets"

    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    task_type: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(100), default="curated")
    enabled: Mapped[bool] = mapped_column(default=True)


class EvalCase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "eval_cases"
    __table_args__ = (Index("ix_eval_cases_dataset_order", "dataset_id", "case_order"),)

    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("eval_datasets.id", ondelete="CASCADE"), nullable=False
    )
    case_order: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    input_payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    expected_output: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    expected_tools: Mapped[list[str]] = mapped_column(JSON, default=list)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)


class EvalRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "eval_runs"
    __table_args__ = (Index("ix_eval_runs_dataset_created", "dataset_id", "created_at"),)

    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("eval_datasets.id", ondelete="CASCADE"), nullable=False
    )
    triggered_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(30), default="queued")
    graph_version: Mapped[str] = mapped_column(String(80), nullable=False)
    model_config: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    summary_metrics: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    total_cases: Mapped[int] = mapped_column(Integer, default=0)
    passed_cases: Mapped[int] = mapped_column(Integer, default=0)


class EvalResult(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "eval_results"
    __table_args__ = (Index("ix_eval_results_run_case", "eval_run_id", "eval_case_id"),)

    eval_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False
    )
    eval_case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("eval_cases.id", ondelete="CASCADE"), nullable=False
    )
    agent_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    passed: Mapped[bool] = mapped_column(default=False)
    score: Mapped[float] = mapped_column(Float, default=0)
    metrics: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    actual_output: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    trajectory: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    evaluator_comments: Mapped[str] = mapped_column(Text, default="")
