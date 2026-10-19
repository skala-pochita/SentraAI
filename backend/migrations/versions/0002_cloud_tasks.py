"""Agent·Cloud 감사 테이블 (cloud_tasks, cloud_task_events, cloud_approvals, remediation_tickets)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-19
"""

from alembic import op

from core.db.models import CloudApproval, CloudTask, CloudTaskEvent, RemediationTicket

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TABLES = (CloudTask, CloudTaskEvent, CloudApproval, RemediationTicket)


def upgrade() -> None:
    bind = op.get_bind()
    for model in TABLES:
        model.__table__.create(bind, checkfirst=True)  # type: ignore[attr-defined]


def downgrade() -> None:
    bind = op.get_bind()
    for model in reversed(TABLES):
        model.__table__.drop(bind, checkfirst=True)  # type: ignore[attr-defined]
