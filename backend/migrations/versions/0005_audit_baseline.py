"""audits.baseline_audit_id (수정본 재업로드 비교의 기준 감사)

0001 은 현재 모델로 테이블을 만들기 때문에 새 DB 에는 이미 열이 있을 수 있다 → IF NOT EXISTS.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-23
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE audits ADD COLUMN IF NOT EXISTS baseline_audit_id uuid REFERENCES audits(id)")


def downgrade() -> None:
    op.execute("ALTER TABLE audits DROP COLUMN IF EXISTS baseline_audit_id")
