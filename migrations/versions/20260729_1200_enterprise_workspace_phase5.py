"""enterprise workspace phase 5

Revision ID: 20260729_1200
Revises: 20260728_1800
Create Date: 2026-07-29 12:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260729_1200"
down_revision = "20260728_1800"
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


def _has_index(bind, table_name: str, index_name: str) -> bool:
    try:
        return any(idx.get("name") == index_name for idx in inspect(bind).get_indexes(table_name))
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


def _create_index_once(bind, index_name: str, table_name: str, columns: list[str]) -> None:
    if not _has_index(bind, table_name, index_name):
        op.create_index(index_name, table_name, columns, unique=False)


def _drop_index_once(bind, index_name: str, table_name: str) -> None:
    if _has_index(bind, table_name, index_name):
        op.drop_index(index_name, table_name=table_name)


def upgrade():
    bind = op.get_bind()

    if _has_table(bind, "structure_contacts"):
        _add_column_once(
            bind,
            "structure_contacts",
            sa.Column("preferred_communication", sa.String(length=80), nullable=True),
        )

    if _has_table(bind, "structure_services"):
        _add_column_once(bind, "structure_services", sa.Column("notes", sa.Text(), nullable=True))

    if _has_table(bind, "structure_coverage_areas"):
        for column in (
            sa.Column("region", sa.String(length=120), nullable=True),
            sa.Column("administrative_code", sa.String(length=64), nullable=True),
            sa.Column("geometry_kind", sa.String(length=32), nullable=True),
            sa.Column("geometry_data_json", sa.Text(), nullable=True),
        ):
            _add_column_once(bind, "structure_coverage_areas", column)
        for index_name, columns in (
            ("ix_structure_coverage_areas_region", ["region"]),
            ("ix_structure_coverage_areas_administrative_code", ["administrative_code"]),
        ):
            _create_index_once(bind, index_name, "structure_coverage_areas", columns)


def downgrade():
    bind = op.get_bind()

    if _has_table(bind, "structure_coverage_areas"):
        for index_name in (
            "ix_structure_coverage_areas_administrative_code",
            "ix_structure_coverage_areas_region",
        ):
            _drop_index_once(bind, index_name, "structure_coverage_areas")
        for column_name in (
            "geometry_data_json",
            "geometry_kind",
            "administrative_code",
            "region",
        ):
            _drop_column_once(bind, "structure_coverage_areas", column_name)

    if _has_table(bind, "structure_services"):
        _drop_column_once(bind, "structure_services", "notes")

    if _has_table(bind, "structure_contacts"):
        _drop_column_once(bind, "structure_contacts", "preferred_communication")
