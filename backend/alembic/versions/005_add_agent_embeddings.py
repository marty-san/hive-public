"""add agent embeddings

Revision ID: 005
Revises: 004
Create Date: 2026-02-06

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '005'
down_revision = '004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add embedding column to agents table
    op.add_column('agents',
        sa.Column('embedding', sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    # Remove embedding column from agents table
    op.drop_column('agents', 'embedding')
