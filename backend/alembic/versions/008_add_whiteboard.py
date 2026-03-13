"""Add whiteboard tables.

Revision ID: 008
Revises: 007
Create Date: 2026-03-12

"""
from alembic import op
import sqlalchemy as sa

revision = '008'
down_revision = '007'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'whiteboard_entries',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('conversation_id', sa.String(), sa.ForeignKey('conversations.id'), nullable=False, index=True),
        sa.Column('key', sa.String(50), nullable=False),
        sa.Column('entry_type', sa.String(20), nullable=False),
        sa.Column('value', sa.String(240), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('last_author_id', sa.String(), nullable=True),
        sa.Column('last_author_type', sa.String(10), nullable=True),
        sa.Column('last_author_name', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('conversation_id', 'key', name='uq_whiteboard_conv_key'),
    )

    op.create_table(
        'whiteboard_log',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('conversation_id', sa.String(), sa.ForeignKey('conversations.id'), nullable=False, index=True),
        sa.Column('entry_key', sa.String(50), nullable=False),
        sa.Column('entry_type', sa.String(20), nullable=True),
        sa.Column('action', sa.String(10), nullable=False),
        sa.Column('author_id', sa.String(), nullable=True),
        sa.Column('author_type', sa.String(10), nullable=True),
        sa.Column('author_name', sa.String(), nullable=True),
        sa.Column('old_value', sa.Text(), nullable=True),
        sa.Column('new_value', sa.Text(), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('message_id', sa.String(), sa.ForeignKey('messages.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), index=True),
    )


def downgrade():
    op.drop_table('whiteboard_log')
    op.drop_table('whiteboard_entries')
