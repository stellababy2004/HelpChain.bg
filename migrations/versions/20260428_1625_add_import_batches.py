"""add import_batches table for admin import express

Revision ID: 20260428_1625
Revises: 20260411_0015
Create Date: 2026-04-28 16:25:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "20260428_1625"
down_revision = "20260429_1100"
branch_labels = None
depends_on = None


def _has_table(bind, table_name: str) -> bool:
    insp = inspect(bind)
    try:
        return table_name in insp.get_table_names()
    except Exception:
        return False


def upgrade():
    bind = op.get_bind()
    if _has_table(bind, "import_batches"):
        return

    op.create_table(
        "import_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column(
            "source_type",
            sa.String(length=40),
            nullable=False,
            server_default="csv",
        ),
        sa.Column(
            "target_type",
            sa.String(length=80),
            nullable=False,
            server_default="professional_leads",
        ),
        sa.Column(
            "status",
            sa.String(length=30),
            nullable=False,
            server_default="preview",
        ),
        sa.Column("created_by_admin_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("imported_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mapping_json", sa.Text(), nullable=True),
        sa.Column("errors_json", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by_admin_id"],
            ["admin_users.id"],
            name="fk_import_batches_created_by_admin_id_admin_users",
        ),
    )
    op.create_index(
        "ix_import_batches_source_type", "import_batches", ["source_type"], unique=False
    )
    op.create_index(
        "ix_import_batches_target_type", "import_batches", ["target_type"], unique=False
    )
    op.create_index(
        "ix_import_batches_status", "import_batches", ["status"], unique=False
    )
    op.create_index(
        "ix_import_batches_created_by_admin_id",
        "import_batches",
        ["created_by_admin_id"],
        unique=False,
    )
    op.create_index(
        "ix_import_batches_created_at", "import_batches", ["created_at"], unique=False
    )


def downgrade():
    bind = op.get_bind()
    if not _has_table(bind, "import_batches"):
        return

    op.drop_index("ix_import_batches_created_at", table_name="import_batches")
    op.drop_index("ix_import_batches_created_by_admin_id", table_name="import_batches")
    op.drop_index("ix_import_batches_status", table_name="import_batches")
    op.drop_index("ix_import_batches_target_type", table_name="import_batches")
    op.drop_index("ix_import_batches_source_type", table_name="import_batches")
    op.drop_table("import_batches")

