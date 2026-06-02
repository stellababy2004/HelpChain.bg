"""add import enrichment counters and professional lead activity audit

Revision ID: 20260602_1545
Revises: 20260428_1625
Create Date: 2026-06-02 15:45:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260602_1545"
down_revision = "20260428_1625"
branch_labels = None
depends_on = None


def _has_table(bind, table_name: str) -> bool:
    insp = inspect(bind)
    try:
        return table_name in insp.get_table_names()
    except Exception:
        return False


def _has_column(bind, table_name: str, column_name: str) -> bool:
    insp = inspect(bind)
    try:
        return column_name in {col["name"] for col in insp.get_columns(table_name)}
    except Exception:
        return False


def upgrade():
    bind = op.get_bind()

    if _has_table(bind, "import_batches"):
        for column_name in (
            "created_count",
            "updated_count",
            "skipped_duplicate_count",
            "rejected_count",
        ):
            if not _has_column(bind, "import_batches", column_name):
                op.add_column(
                    "import_batches",
                    sa.Column(column_name, sa.Integer(), nullable=False, server_default="0"),
                )

    if not _has_table(bind, "professional_lead_activities"):
        op.create_table(
            "professional_lead_activities",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("professional_lead_id", sa.Integer(), nullable=False),
            sa.Column("admin_user_id", sa.Integer(), nullable=True),
            sa.Column("action", sa.String(length=64), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(
                ["professional_lead_id"],
                ["professional_leads.id"],
                name="fk_professional_lead_activities_professional_lead_id_professional_leads",
            ),
            sa.ForeignKeyConstraint(
                ["admin_user_id"],
                ["admin_users.id"],
                name="fk_professional_lead_activities_admin_user_id_admin_users",
            ),
        )
        op.create_index(
            "ix_professional_lead_activities_professional_lead_id",
            "professional_lead_activities",
            ["professional_lead_id"],
            unique=False,
        )
        op.create_index(
            "ix_professional_lead_activities_admin_user_id",
            "professional_lead_activities",
            ["admin_user_id"],
            unique=False,
        )
        op.create_index(
            "ix_professional_lead_activities_action",
            "professional_lead_activities",
            ["action"],
            unique=False,
        )
        op.create_index(
            "ix_professional_lead_activities_created_at",
            "professional_lead_activities",
            ["created_at"],
            unique=False,
        )


def downgrade():
    bind = op.get_bind()

    if _has_table(bind, "professional_lead_activities"):
        op.drop_index(
            "ix_professional_lead_activities_created_at",
            table_name="professional_lead_activities",
        )
        op.drop_index(
            "ix_professional_lead_activities_action",
            table_name="professional_lead_activities",
        )
        op.drop_index(
            "ix_professional_lead_activities_admin_user_id",
            table_name="professional_lead_activities",
        )
        op.drop_index(
            "ix_professional_lead_activities_professional_lead_id",
            table_name="professional_lead_activities",
        )
        op.drop_table("professional_lead_activities")

    if _has_table(bind, "import_batches"):
        for column_name in (
            "rejected_count",
            "skipped_duplicate_count",
            "updated_count",
            "created_count",
        ):
            if _has_column(bind, "import_batches", column_name):
                op.drop_column("import_batches", column_name)
