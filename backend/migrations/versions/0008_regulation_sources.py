"""Add regulatory knowledge types without changing the Finding/report schema."""
from alembic import op

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None
OLD = ('owasp_top10', 'owasp_llm', 'owasp_agentic', 'cwe', 'framework', 'policy', 'past_case')


def _replace(values):
    op.drop_constraint('ck_source_type', 'kb_documents', type_='check')
    op.create_check_constraint('ck_source_type', 'kb_documents', 'source_type IN (' + ','.join(repr(v) for v in values) + ')')


def upgrade():
    _replace((*OLD, 'kr_law', 'kr_guideline'))
    op.execute("CREATE UNIQUE INDEX uq_kb_regulation_ref ON kb_documents (ref_code) WHERE source_type IN ('kr_law','kr_guideline')")


def downgrade():
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM kb_documents WHERE source_type IN ('kr_law','kr_guideline')) THEN RAISE EXCEPTION 'Remove regulation knowledge explicitly before downgrade'; END IF; END $$")
    op.drop_index('uq_kb_regulation_ref', table_name='kb_documents')
    _replace(OLD)
