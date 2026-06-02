"""merge import batches into active migration chain

Revision ID: 8e0439604576
Revises: 20260428_1625, 20260519_1000
Create Date: 2026-06-02 10:33:39.169769

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8e0439604576'
down_revision = ('20260428_1625', '20260519_1000')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
