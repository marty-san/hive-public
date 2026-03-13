"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-02-03

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This migration is a placeholder since tables were created via Base.metadata.create_all
    # All tables should already exist from init_db()
    pass


def downgrade() -> None:
    pass
