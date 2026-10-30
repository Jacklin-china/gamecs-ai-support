"""Add public ticket conversation and player feedback.

Revision ID: d4a12ef75b20
Revises: b31b58408478
"""
from alembic import op
import sqlalchemy as sa

revision = 'd4a12ef75b20'
down_revision = 'b31b58408478'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('ticket_messages',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('ticket_id', sa.Integer(), sa.ForeignKey('tickets.id'), nullable=False),
        sa.Column('author_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('author_role', sa.String(20), nullable=False),
        sa.Column('request_id', sa.String(64), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('ticket_id', 'author_id', 'request_id', name='uq_ticket_message_request'))
    op.create_index('ix_ticket_messages_ticket_id', 'ticket_messages', ['ticket_id'])
    op.create_table('ticket_ratings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('ticket_id', sa.Integer(), sa.ForeignKey('tickets.id'), nullable=False, unique=True),
        sa.Column('player_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('score', sa.Integer(), nullable=False),
        sa.Column('comment', sa.String(500), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False))


def downgrade():
    op.drop_table('ticket_ratings')
    op.drop_index('ix_ticket_messages_ticket_id', table_name='ticket_messages')
    op.drop_table('ticket_messages')
