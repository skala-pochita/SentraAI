"""trace_spans.trace_id (Observability: 감사 1건 = trace_id 1개)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trace_spans", sa.Column("trace_id", sa.Text(), nullable=True))
    op.create_index("ix_trace_spans_trace_id", "trace_spans", ["trace_id"])


def downgrade() -> None:
    op.drop_index("ix_trace_spans_trace_id", table_name="trace_spans")
    op.drop_column("trace_spans", "trace_id")
