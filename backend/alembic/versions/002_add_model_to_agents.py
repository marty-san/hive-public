"""Add model field to agents

Revision ID: 002
Revises: 001
Create Date: 2026-02-03

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add model column to agents table
    op.add_column('agents', sa.Column('model', sa.String(), nullable=True))


def downgrade() -> None:
    # Remove model column from agents table
    op.drop_column('agents', 'model')
