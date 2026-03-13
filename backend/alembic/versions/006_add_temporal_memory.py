"""add temporal memory fields

Revision ID: 006
Revises: 005
Create Date: 2026-03-10

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '006'
down_revision = '005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add bitemporal and structured-attribute columns to agent_episodic_memories
    op.add_column('agent_episodic_memories',
        sa.Column('valid_from', sa.DateTime(), nullable=True))
    op.add_column('agent_episodic_memories',
        sa.Column('valid_until', sa.DateTime(), nullable=True))
    op.add_column('agent_episodic_memories',
        sa.Column('asserted_at', sa.DateTime(), nullable=True))
    op.add_column('agent_episodic_memories',
        sa.Column('asserted_until', sa.DateTime(), nullable=True))
    op.add_column('agent_episodic_memories',
        sa.Column('superseded_by', sa.String(), nullable=True))
    op.add_column('agent_episodic_memories',
        sa.Column('occurred_at', sa.DateTime(), nullable=True))
    op.add_column('agent_episodic_memories',
        sa.Column('importance', sa.SmallInteger(), nullable=True))

    # Backfill effective and asserted time from created_at for all existing rows
    op.execute(
        "UPDATE agent_episodic_memories SET valid_from = created_at WHERE valid_from IS NULL"
    )
    op.execute(
        "UPDATE agent_episodic_memories SET asserted_at = created_at WHERE asserted_at IS NULL"
    )

    # Rename last_accessed -> last_accessed_at (SQLite 3.25+ supports RENAME COLUMN)
    with op.batch_alter_table('agent_episodic_memories') as batch_op:
        batch_op.alter_column('last_accessed', new_column_name='last_accessed_at')


def downgrade() -> None:
    with op.batch_alter_table('agent_episodic_memories') as batch_op:
        batch_op.alter_column('last_accessed_at', new_column_name='last_accessed')

    op.drop_column('agent_episodic_memories', 'importance')
    op.drop_column('agent_episodic_memories', 'occurred_at')
    op.drop_column('agent_episodic_memories', 'superseded_by')
    op.drop_column('agent_episodic_memories', 'asserted_until')
    op.drop_column('agent_episodic_memories', 'asserted_at')
    op.drop_column('agent_episodic_memories', 'valid_until')
    op.drop_column('agent_episodic_memories', 'valid_from')
