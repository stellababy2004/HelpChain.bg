"""add metric registry and score explanations

Revision ID: 20260728_0900
Revises: 0452fbf5a79f
Create Date: 2026-07-28 09:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260728_0900"
down_revision = "0452fbf5a79f"
branch_labels = None
depends_on = None


def _has_table(bind, table_name: str) -> bool:
    try:
        return table_name in inspect(bind).get_table_names()
    except Exception:
        return False


def _has_index(bind, table_name: str, index_name: str) -> bool:
    try:
        return any(
            idx.get("name") == index_name
            for idx in inspect(bind).get_indexes(table_name)
        )
    except Exception:
        return False


def _create_index_once(bind, index_name: str, table_name: str, columns: list[str], *, unique: bool = False) -> None:
    if not _has_index(bind, table_name, index_name):
        op.create_index(index_name, table_name, columns, unique=unique)


def upgrade():
    bind = op.get_bind()

    if not _has_table(bind, "metric_definitions"):
        op.create_table(
            "metric_definitions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("metric_key", sa.String(length=120), nullable=False),
            sa.Column("display_name", sa.String(length=180), nullable=False),
            sa.Column("formula_version", sa.String(length=40), nullable=False),
            sa.Column("owner", sa.String(length=120), nullable=False),
            sa.Column("refresh_interval_seconds", sa.Integer(), nullable=False),
            sa.Column("confidence", sa.String(length=30), nullable=False, server_default="low"),
            sa.Column("source_tables_json", sa.Text(), nullable=False),
            sa.Column("query_origin", sa.Text(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("metric_key", name="uq_metric_definitions_metric_key"),
        )
    _create_index_once(bind, "ix_metric_definitions_metric_key", "metric_definitions", ["metric_key"])

    if not _has_table(bind, "metric_runs"):
        op.create_table(
            "metric_runs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("metric_key", sa.String(length=120), nullable=False),
            sa.Column("formula_version", sa.String(length=40), nullable=False),
            sa.Column("value_json", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="unavailable"),
            sa.Column("unavailable_reason", sa.Text(), nullable=True),
            sa.Column("source_tables_json", sa.Text(), nullable=False),
            sa.Column("query_origin", sa.Text(), nullable=False),
            sa.Column("confidence", sa.String(length=30), nullable=False, server_default="low"),
            sa.Column("source_row_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
            sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
            sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    for index_name, columns in (
        ("ix_metric_runs_metric_key", ["metric_key"]),
        ("ix_metric_runs_status", ["status"]),
        ("ix_metric_runs_period_start", ["period_start"]),
        ("ix_metric_runs_period_end", ["period_end"]),
        ("ix_metric_runs_computed_at", ["computed_at"]),
    ):
        _create_index_once(bind, index_name, "metric_runs", columns)

    if not _has_table(bind, "score_explanations"):
        op.create_table(
            "score_explanations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("score_key", sa.String(length=120), nullable=False),
            sa.Column("subject_type", sa.String(length=80), nullable=False),
            sa.Column("subject_id", sa.String(length=120), nullable=False),
            sa.Column("total_score", sa.Integer(), nullable=False),
            sa.Column("formula_version", sa.String(length=40), nullable=False),
            sa.Column("confidence", sa.String(length=30), nullable=False, server_default="low"),
            sa.Column("component_list_json", sa.Text(), nullable=False),
            sa.Column("evidence_json", sa.Text(), nullable=False),
            sa.Column("originating_event_ids_json", sa.Text(), nullable=False),
            sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    for index_name, columns in (
        ("ix_score_explanations_score_key", ["score_key"]),
        ("ix_score_explanations_subject_type", ["subject_type"]),
        ("ix_score_explanations_subject_id", ["subject_id"]),
        ("ix_score_explanations_computed_at", ["computed_at"]),
        (
            "ix_score_explanations_subject_score",
            ["subject_type", "subject_id", "score_key"],
        ),
    ):
        _create_index_once(bind, index_name, "score_explanations", columns)


def downgrade():
    bind = op.get_bind()
    if _has_table(bind, "score_explanations"):
        op.drop_table("score_explanations")
    if _has_table(bind, "metric_runs"):
        op.drop_table("metric_runs")
    if _has_table(bind, "metric_definitions"):
        op.drop_table("metric_definitions")
