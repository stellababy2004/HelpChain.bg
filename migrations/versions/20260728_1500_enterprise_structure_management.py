"""enterprise structure management

Revision ID: 20260728_1500
Revises: 20260728_0900
Create Date: 2026-07-28 15:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260728_1500"
down_revision = "20260728_0900"
branch_labels = None
depends_on = None


def _has_table(bind, table_name: str) -> bool:
    try:
        return table_name in inspect(bind).get_table_names()
    except Exception:
        return False


def _has_column(bind, table_name: str, column_name: str) -> bool:
    try:
        return any(
            col.get("name") == column_name
            for col in inspect(bind).get_columns(table_name)
        )
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


def _create_index_once(bind, index_name: str, table_name: str, columns: list[str], *, unique: bool = False) -> None:
    if not _has_index(bind, table_name, index_name):
        op.create_index(index_name, table_name, columns, unique=unique)


def _drop_index_once(bind, index_name: str, table_name: str) -> None:
    if _has_index(bind, table_name, index_name):
        op.drop_index(index_name, table_name=table_name)


def _drop_column_once(bind, table_name: str, column_name: str) -> None:
    if _has_column(bind, table_name, column_name):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_column(column_name)


def upgrade():
    bind = op.get_bind()

    if _has_table(bind, "structures"):
        for column in (
            sa.Column("organization_type", sa.String(length=64), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("legal_name", sa.String(length=255), nullable=True),
            sa.Column("registration_number", sa.String(length=120), nullable=True),
            sa.Column("website", sa.String(length=255), nullable=True),
            sa.Column("email", sa.String(length=255), nullable=True),
            sa.Column("phone", sa.String(length=80), nullable=True),
            sa.Column("emergency_phone", sa.String(length=80), nullable=True),
            sa.Column("opening_hours", sa.Text(), nullable=True),
            sa.Column("head_office", sa.Text(), nullable=True),
            sa.Column("departments_json", sa.Text(), nullable=True),
            sa.Column("territory", sa.String(length=255), nullable=True),
            sa.Column("capabilities_json", sa.Text(), nullable=True),
            sa.Column("languages_json", sa.Text(), nullable=True),
            sa.Column("priority_domains_json", sa.Text(), nullable=True),
            sa.Column("accepted_case_types_json", sa.Text(), nullable=True),
            sa.Column("required_documents_json", sa.Text(), nullable=True),
            sa.Column("supported_populations_json", sa.Text(), nullable=True),
            sa.Column("risk_level", sa.String(length=32), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        ):
            _add_column_once(bind, "structures", column)
        _create_index_once(bind, "ix_structures_organization_type", "structures", ["organization_type"])
        _create_index_once(bind, "ix_structures_territory", "structures", ["territory"])

    if _has_table(bind, "structure_services"):
        for column in (
            sa.Column("category", sa.String(length=80), nullable=True),
            sa.Column("availability", sa.String(length=80), nullable=True),
            sa.Column("capacity", sa.Integer(), nullable=True),
            sa.Column("responsible_professionals_json", sa.Text(), nullable=True),
            sa.Column("opening_hours", sa.Text(), nullable=True),
            sa.Column("coverage", sa.Text(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        ):
            _add_column_once(bind, "structure_services", column)
        _create_index_once(bind, "ix_structure_services_category", "structure_services", ["category"])

    if not _has_table(bind, "structure_contacts"):
        op.create_table(
            "structure_contacts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("structure_id", sa.Integer(), nullable=False),
            sa.Column("contact_type", sa.String(length=40), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=True),
            sa.Column("role", sa.String(length=120), nullable=True),
            sa.Column("email", sa.String(length=255), nullable=True),
            sa.Column("phone", sa.String(length=80), nullable=True),
            sa.Column("availability", sa.Text(), nullable=True),
            sa.Column("escalation_order", sa.Integer(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["structure_id"], ["structures.id"]),
        )
    for index_name, columns in (
        ("ix_structure_contacts_structure_id", ["structure_id"]),
        ("ix_structure_contacts_contact_type", ["contact_type"]),
        ("ix_structure_contacts_created_at", ["created_at"]),
        ("ix_structure_contacts_structure_type", ["structure_id", "contact_type"]),
        ("ix_structure_contacts_structure_active", ["structure_id", "is_active"]),
    ):
        _create_index_once(bind, index_name, "structure_contacts", columns)

    if not _has_table(bind, "structure_coverage_areas"):
        op.create_table(
            "structure_coverage_areas",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("structure_id", sa.Integer(), nullable=False),
            sa.Column("area_type", sa.String(length=40), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("postal_code", sa.String(length=32), nullable=True),
            sa.Column("department", sa.String(length=120), nullable=True),
            sa.Column("coverage_radius_km", sa.Float(), nullable=True),
            sa.Column("population_served", sa.Integer(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["structure_id"], ["structures.id"]),
            sa.UniqueConstraint(
                "structure_id",
                "area_type",
                "name",
                name="uq_structure_coverage_structure_type_name",
            ),
        )
    for index_name, columns in (
        ("ix_structure_coverage_areas_structure_id", ["structure_id"]),
        ("ix_structure_coverage_areas_area_type", ["area_type"]),
        ("ix_structure_coverage_areas_postal_code", ["postal_code"]),
        ("ix_structure_coverage_areas_department", ["department"]),
        ("ix_structure_coverage_areas_created_at", ["created_at"]),
        ("ix_structure_coverage_structure_type", ["structure_id", "area_type"]),
        ("ix_structure_coverage_structure_active", ["structure_id", "is_active"]),
    ):
        _create_index_once(bind, index_name, "structure_coverage_areas", columns)


def downgrade():
    bind = op.get_bind()
    if _has_table(bind, "structure_coverage_areas"):
        op.drop_table("structure_coverage_areas")
    if _has_table(bind, "structure_contacts"):
        op.drop_table("structure_contacts")
    if _has_table(bind, "structure_services"):
        _drop_index_once(bind, "ix_structure_services_category", "structure_services")
        for column_name in (
            "updated_at",
            "coverage",
            "opening_hours",
            "responsible_professionals_json",
            "capacity",
            "availability",
            "category",
        ):
            _drop_column_once(bind, "structure_services", column_name)
    if _has_table(bind, "structures"):
        _drop_index_once(bind, "ix_structures_territory", "structures")
        _drop_index_once(bind, "ix_structures_organization_type", "structures")
        for column_name in (
            "updated_at",
            "risk_level",
            "supported_populations_json",
            "required_documents_json",
            "accepted_case_types_json",
            "priority_domains_json",
            "languages_json",
            "capabilities_json",
            "territory",
            "departments_json",
            "head_office",
            "opening_hours",
            "emergency_phone",
            "phone",
            "email",
            "website",
            "registration_number",
            "legal_name",
            "description",
            "organization_type",
        ):
            _drop_column_once(bind, "structures", column_name)
