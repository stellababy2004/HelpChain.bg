"""merge security governance migration heads

Revision ID: 0452fbf5a79f
Revises: 20260602_1545, 8e0439604576
Create Date: 2026-06-02 13:21:01.088755

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0452fbf5a79f'
down_revision = ('20260602_1545', '8e0439604576')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
