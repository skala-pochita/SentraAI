"""Persist human-readable audit names.

Revision ID: 0009
Revises: 0008
"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Both branches shipped revision 0008 with different changes. Reconcile
    # either database state without dropping existing audit names or documents.
    op.execute("ALTER TABLE audits ADD COLUMN IF NOT EXISTS name TEXT")
    op.drop_constraint("ck_source_type", "kb_documents", type_="check")
    op.create_check_constraint(
        "ck_source_type", "kb_documents",
        "source_type IN ('owasp_top10', 'owasp_llm', 'owasp_agentic', 'cwe', "
        "'framework', 'policy', 'past_case', 'kr_law', 'kr_guideline')",
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_kb_regulation_ref "
        "ON kb_documents (ref_code) WHERE source_type IN ('kr_law','kr_guideline')"
    )


def downgrade() -> None:
    op.drop_column("audits", "name")
