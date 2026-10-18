"""Persist first setup progress without changing existing application data.

Revision ID: 08b5d0e3f9c7
Revises: f7a4c9d2e8b6
"""
from alembic import op
import sqlalchemy as sa

revision = '08b5d0e3f9c7'
down_revision = 'f7a4c9d2e8b6'
branch_labels = None
depends_on = None


def upgrade():
    # New installations create setup metadata before the operator runs migrations.
    if not sa.inspect(op.get_bind()).has_table('setup_state'):
        op.create_table('setup_state',
                        sa.Column('id', sa.Integer(), nullable=False),
                        sa.Column('complete', sa.Boolean(), nullable=False),
                        sa.Column('code_hash', sa.String(64)),
                        sa.Column('session_hash', sa.String(64)),
                        sa.Column('progress', sa.JSON(), nullable=False),
                        sa.Column('settings', sa.LargeBinary()),
                        sa.PrimaryKeyConstraint('id', name=op.f('pk_setup_state')))


def downgrade():
    op.drop_table('setup_state')
