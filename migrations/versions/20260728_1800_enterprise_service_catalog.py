"""enterprise service catalog

Revision ID: 20260728_1800
Revises: 20260728_1500
Create Date: 2026-07-28 18:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260728_1800"
down_revision = "20260728_1500"
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
    if not _has_table(bind, "structure_services"):
        return

    for column in (
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("priority", sa.String(length=32), nullable=True),
        sa.Column("response_sla_hours", sa.Integer(), nullable=True),
        sa.Column("target_population", sa.Text(), nullable=True),
        sa.Column("eligibility", sa.Text(), nullable=True),
        sa.Column("required_documents_json", sa.Text(), nullable=True),
        sa.Column("languages_json", sa.Text(), nullable=True),
        sa.Column("contact_name", sa.String(length=255), nullable=True),
        sa.Column("contact_email", sa.String(length=255), nullable=True),
        sa.Column("contact_phone", sa.String(length=80), nullable=True),
        sa.Column("tags_json", sa.Text(), nullable=True),
        sa.Column("risk_level", sa.String(length=32), nullable=True),
        sa.Column("territory", sa.String(length=255), nullable=True),
        sa.Column("referral_required", sa.Boolean(), nullable=True),
        sa.Column("emergency_support", sa.Boolean(), nullable=True),
    ):
        _add_column_once(bind, "structure_services", column)

    for index_name, columns in (
        ("ix_structure_services_structure_status", ["structure_id", "status"]),
        ("ix_structure_services_structure_category", ["structure_id", "category"]),
        ("ix_structure_services_structure_availability", ["structure_id", "availability"]),
        ("ix_structure_services_priority", ["priority"]),
        ("ix_structure_services_risk_level", ["risk_level"]),
        ("ix_structure_services_territory", ["territory"]),
    ):
        _create_index_once(bind, index_name, "structure_services", columns)


def downgrade():
    bind = op.get_bind()
    if not _has_table(bind, "structure_services"):
        return

    for index_name in (
        "ix_structure_services_territory",
        "ix_structure_services_risk_level",
        "ix_structure_services_priority",
        "ix_structure_services_structure_availability",
        "ix_structure_services_structure_category",
        "ix_structure_services_structure_status",
    ):
        _drop_index_once(bind, index_name, "structure_services")

    for column_name in (
        "emergency_support",
        "referral_required",
        "territory",
        "risk_level",
        "tags_json",
        "contact_phone",
        "contact_email",
        "contact_name",
        "languages_json",
        "required_documents_json",
        "eligibility",
        "target_population",
        "response_sla_hours",
        "priority",
        "status",
        "description",
    ):
        _drop_column_once(bind, "structure_services", column_name)
