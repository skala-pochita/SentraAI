"""Evidence-backed paths; 0001 creates current models, so additions are idempotent."""
from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('ALTER TABLE findings ADD COLUMN IF NOT EXISTS subject text')
    op.execute('ALTER TABLE risk_paths ADD COLUMN IF NOT EXISTS payload jsonb')
    op.execute('ALTER TABLE risk_paths ADD COLUMN IF NOT EXISTS correlation_finding_id uuid REFERENCES findings(id)')
    op.execute("""DO $$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='risk_paths'::regclass AND conname='uq_risk_path_correlation') THEN
        ALTER TABLE risk_paths ADD CONSTRAINT uq_risk_path_correlation UNIQUE (correlation_finding_id);
      END IF;
    END $$""")
    op.execute('ALTER TABLE probe_runs ADD COLUMN IF NOT EXISTS verdict text')


def downgrade() -> None:
    op.drop_column('probe_runs', 'verdict')
    op.drop_constraint('uq_risk_path_correlation', 'risk_paths', type_='unique')
    op.drop_column('risk_paths', 'correlation_finding_id')
    op.drop_column('risk_paths', 'payload')
    op.drop_column('findings', 'subject')
