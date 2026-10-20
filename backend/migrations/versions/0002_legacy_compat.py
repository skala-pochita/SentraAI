"""Compatibility marker for the retired Cloud schema revision.

Existing installations can still be stamped 0002 after Cloud support was
removed. Keep this revision in the chain so they can advance to 0003 without
resetting data. New installations do not need the retired Cloud tables.

Revision ID: 0002
Revises: 0001
"""

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Retain existing tables; do not create retired features on fresh installs."""


def downgrade() -> None:
    """Preserve legacy data when crossing the compatibility marker."""
