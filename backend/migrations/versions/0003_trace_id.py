"""trace_spans.trace_id (Observability: 감사 1건 = trace_id 1개)

0001 은 현재 모델로 테이블을 만들기 때문에 새 DB 에는 이미 열이 있을 수 있다 → IF NOT EXISTS.
(Cloud 감사 제거로 0002 가 삭제되어 0001 다음 리비전이다.)

Revision ID: 0003
Revises: 0001
Create Date: 2026-10-20
"""

from alembic import op

revision = "0003"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE trace_spans ADD COLUMN IF NOT EXISTS trace_id text")
    op.execute("CREATE INDEX IF NOT EXISTS ix_trace_spans_trace_id ON trace_spans (trace_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_trace_spans_trace_id")
    op.execute("ALTER TABLE trace_spans DROP COLUMN IF EXISTS trace_id")
