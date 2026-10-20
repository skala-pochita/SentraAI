export type Severity = 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW' | 'INFO'
export interface Paged<T> { items: T[]; total: number; page: number; size: number }
export interface Target { id: string; type: 'repo' | 'agent'; name: string; ref: string }
export interface Audit {
  id: string; status: string; mode: string; current_node: string | null; progress: number
  created_at: string; started_at: string | null; finished_at: string | null
  summary: { by_severity?: Record<string, number>; total?: number; usd?: number; tokens?: number } | null
  pending_approval_id?: string | null
}
export interface Evidence { kind: string; file_path?: string; line_start?: number; snippet_masked?: string; probe_id?: string }
export interface Finding {
  id: string; audit_id: string; target_id: string; source: string; category: string; rule_id: string | null
  title: string; severity: Severity; confidence: number; status: string; evidence: Evidence[]
  reasoning_summary: string; tools_used: string[]; standard_refs: string[]; fix_hint: string | null
  fix_proposals?: FixProposal[]; risk_paths?: RiskPath[]
}
export interface RiskPath { id: string; rule_id: string; severity: Severity; title: string; finding_ids: string[]; explanation: string }
export interface FixProposal { id: string; diff: string; merge_status: string | null; branch: string | null; patch_check_passed: boolean; finding_ids: string[]; kind: string }
export interface Approval { id: string; audit_id: string; status: string; summary: string; findings: { id: string; title: string; severity: Severity; category: string }[] }

