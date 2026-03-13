"""Add human_votes_on_proposals to session_states.

Revision ID: 007
Revises: 006
Create Date: 2026-03-10

"""
from alembic import op
import sqlalchemy as sa

revision = '007'
down_revision = '006'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('session_states') as batch_op:
        batch_op.add_column(
            sa.Column('human_votes_on_proposals', sa.Boolean(), nullable=True, server_default='0')
        )


def downgrade():
    with op.batch_alter_table('session_states') as batch_op:
        batch_op.drop_column('human_votes_on_proposals')
