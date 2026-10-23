export type Severity = 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW' | 'INFO'
export interface Paged<T> { items: T[]; total: number; page: number; size: number }
export interface Target {
  id: string; type: 'repo' | 'agent'; name: string; ref: string; linked_target_id?: string | null
  config?: { source?: string; runtime?: { supported: boolean; reason: string }; agent_detected?: boolean; project_id?: string; version?: number; previous_target_id?: string | null }
}
export interface Audit {
  name?: string | null
  id: string; status: string; mode: string; current_node: string | null; progress: number
  created_at: string; started_at: string | null; finished_at: string | null
  summary: { by_severity?: Record<string, number>; total?: number; usd?: number; tokens?: number } | null
  pending_approval_id?: string | null
  baseline_audit_id?: string | null; target_ids?: string[]
}
export interface Evidence { label?: string | null; relation?: string | null; kind: string; file_path?: string; line_start?: number; snippet_masked?: string; probe_id?: string; line_end?: number; probe_transcript_ref?: string }
export interface Finding {
  id: string; audit_id: string; target_id: string; source: string; category: string; rule_id: string | null
  title: string; severity: Severity; confidence: number; status: string; evidence: Evidence[]
  reasoning_summary: string; tools_used: string[]; standard_refs: string[]; fix_hint: string | null
  fix_proposals?: FixProposal[]; risk_paths?: RiskPath[]
}
export interface RiskPath { payload?: AttackPath | null; correlation_finding_id?: string | null; id: string; rule_id: string; severity: Severity; title: string; finding_ids: string[]; explanation: string }
export interface FixProposal { id: string; diff: string; merge_status: string | null; branch: string | null; patch_check_passed: boolean; finding_ids: string[]; kind: string }
export interface Approval { id: string; audit_id: string; status: string; summary: string; findings: { id: string; title: string; severity: Severity; category: string }[] }


export type EvidenceLevel = 'EXECUTION' | 'STATIC' | 'CONDITIONAL'
export interface PathRef { evidence_index?: number | null; finding_id: string | null; target_id: string; probe_run_id: string | null; tool_call_index: number | null; file_path: string | null; line_start: number | null; field_path: string | null; value_masked?: string | null }
export interface PathStep { id: string; kind: string; title: string; claim: string; evidence_level: EvidenceLevel; target_id: string; evidence_refs: PathRef[]; conditions: string[] }
export interface PathEdge { id: string; from_step_id: string; to_step_id: string; relation: string; evidence_level: EvidenceLevel; evidence_refs: PathRef[]; conditions: string[] }
export interface CodeReason { kind: 'policy' | 'implementation_candidate' | 'code_finding'; title: string; explanation: string; ref: PathRef; snippet_masked: string }
export interface AttackPath { severity_reason?: string | null; code_reasons?: CodeReason[]; schema_version: 1; target_ids: string[]; steps: PathStep[]; edges: PathEdge[]; missing_evidence: string[]; runtime_mode: 'mock' | 'live' | 'unknown'; observation_source: string }
export interface ProbeEvidence { id: string; probe_id: string; attempt: number; verdict: string; judged_by: string; request: unknown; reply_masked: string; tool_calls: unknown[] }
export type CompareStatus = 'NEW' | 'PERSISTING' | 'RESOLVED' | 'BLOCKED' | 'UNVERIFIED' | 'SAFE'
export interface BaselineCandidate { id: string; mode: string; created_at: string; version: number | null; summary: { total?: number } }
export interface Comparison {
  baseline: { audit_id: string; created_at: string; version: number | null; model_verification: string }
  current: { audit_id: string; created_at: string; version: number | null; model_verification: string }
  summary: Record<'NEW' | 'PERSISTING' | 'RESOLVED' | 'UNVERIFIED', number>
  probes: { probe_id: string; category: string; status: CompareStatus; label: string; reason: string | null; baseline: { attempts: number; successes: number } | null; current: { attempts: number; successes: number } | null }[]
  findings: { status: CompareStatus; label: string; reason: string | null; severity: Severity; title: string; category: string; source: string; location: string | null; baseline_finding_id: string | null; current_finding_id: string | null }[]
  benign: { baseline: string; current: string }
  notes: string[]
}
