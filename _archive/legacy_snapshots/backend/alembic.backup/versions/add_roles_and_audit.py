"""add roles and audit_logs

Revision ID: add_roles_and_audit
Revises: <prev_revision>
Create Date: 2025-09-29
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "add_roles_and_audit"
down_revision = "<prev_revision>"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("role", sa.String(length=50), nullable=False, server_default="user"),
    )
    op.add_column(
        "users", sa.Column("twofa_secret_encrypted", sa.Text(), nullable=True)
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=True),
        sa.Column("target_id", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
    )


def downgrade():
    op.drop_table("audit_logs")
    op.drop_column("users", "twofa_secret_encrypted")
    op.drop_column("users", "role")
