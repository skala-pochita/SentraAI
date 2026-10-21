"""Execution settings with opaque vault references.
Revision ID: 0004
Revises: 0003
"""
from alembic import op
from core.db.models import ExecutionSettings

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    ExecutionSettings.__table__.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    ExecutionSettings.__table__.drop(op.get_bind(), checkfirst=True)
