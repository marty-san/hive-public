"""Add interrupt_requested field to session_states

Revision ID: 003
Revises: 002
Create Date: 2026-02-05

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '003'
down_revision = '6bf58f431e75'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add interrupt_requested column to session_states table
    op.add_column('session_states',
        sa.Column('interrupt_requested', sa.Boolean(),
                  nullable=False, server_default='false')
    )


def downgrade() -> None:
    # Remove interrupt_requested column from session_states table
    op.drop_column('session_states', 'interrupt_requested')
