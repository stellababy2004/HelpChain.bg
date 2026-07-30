"""service workspace editor

Revision ID: 20260729_1600
Revises: 20260729_1200
Create Date: 2026-07-29 16:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260729_1600"
down_revision = "20260729_1200"
branch_labels = None
depends_on = None


def _has_table(bind, table_name: str) -> bool:
    try:
        return table_name in inspect(bind).get_table_names()
    except Exception:
        return False


def _has_column(bind, table_name: str, column_name: str) -> bool:
    try:
        return any(col.get("name") == column_name for col in inspect(bind).get_columns(table_name))
    except Exception:
        return False


def _add_column_once(bind, table_name: str, column: sa.Column) -> None:
    if not _has_column(bind, table_name, column.name):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.add_column(column)


def _drop_column_once(bind, table_name: str, column_name: str) -> None:
    if _has_column(bind, table_name, column_name):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_column(column_name)


def upgrade():
    bind = op.get_bind()
    if not _has_table(bind, "structure_services"):
        return

    for column in (
        sa.Column("available_capacity_override", sa.Integer(), nullable=True),
        sa.Column("waiting_time_minutes", sa.Integer(), nullable=True),
        sa.Column("routing_rules_json", sa.Text(), nullable=True),
    ):
        _add_column_once(bind, "structure_services", column)


def downgrade():
    bind = op.get_bind()
    if not _has_table(bind, "structure_services"):
        return

    for column_name in (
        "routing_rules_json",
        "waiting_time_minutes",
        "available_capacity_override",
    ):
        _drop_column_once(bind, "structure_services", column_name)
