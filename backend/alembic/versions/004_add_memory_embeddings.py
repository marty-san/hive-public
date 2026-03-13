"""add memory embeddings

Revision ID: 004
Revises: 003
Create Date: 2026-02-05

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add embedding column to agent_episodic_memories table
    op.add_column('agent_episodic_memories',
        sa.Column('embedding', sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    # Remove embedding column from agent_episodic_memories table
    op.drop_column('agent_episodic_memories', 'embedding')
