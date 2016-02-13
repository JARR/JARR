"""add updated_date column to article table

Revision ID: 7f973d010686
Revises: 25ca960a207
Create Date: 2016-02-12 21:51:40.868539

"""

# revision identifiers, used by Alembic.
revision = '7f973d010686'
down_revision = '25ca960a207'
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column('article', sa.Column('updated_date', sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column('article', 'updated_date')
