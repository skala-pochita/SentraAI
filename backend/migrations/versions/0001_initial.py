"""initial schema (명세 13장)

Revision ID: 0001
Revises:
Create Date: 2026-10-19
"""

from alembic import op

from core.db.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    # 초기 스키마는 모델 정의(core/db/models.py)를 그대로 생성한다. 이후 변경은 autogenerate 로 추가.
    Base.metadata.create_all(op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind())
