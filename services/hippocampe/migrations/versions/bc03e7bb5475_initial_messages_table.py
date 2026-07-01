"""initial messages table

Revision ID: bc03e7bb5475
Revises: 
Create Date: 2026-07-01 17:13:13.538490

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bc03e7bb5475'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'messages',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('role', sa.String(length=50), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_messages_timestamp', 'messages', [sa.text('timestamp DESC')], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_messages_timestamp', table_name='messages')
    op.drop_table('messages')
