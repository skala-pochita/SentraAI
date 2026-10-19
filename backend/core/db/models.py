"""DB 스키마 (명세 13장). Alembic 마이그레이션의 기준."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIM = 1536


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _fk(target: str, nullable: bool = False, **kw: Any) -> Mapped[Any]:
    return mapped_column(UUID(as_uuid=True), ForeignKey(target, **kw), nullable=nullable)


def _in(col: str, values: tuple[str, ...]) -> CheckConstraint:
    joined = ",".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{col} IN ({joined})", name=f"ck_{col}")


ROLES = ("ADMIN", "DEVELOPER", "AUDITOR")
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")
AUDIT_STATUSES = (
    "QUEUED",
    "RUNNING",
    "WAITING_APPROVAL",
    "WAITING_MERGE",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
)
FINDING_STATUSES = ("OPEN", "FIXING", "RESOLVED", "ACCEPTED_RISK", "FALSE_POSITIVE", "MANUAL_REQUIRED")
KB_SOURCE_TYPES = ("owasp_top10", "owasp_llm", "owasp_agentic", "cwe", "framework", "policy", "past_case")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (_in("role", ROLES),)
    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = _created()


class Target(Base):
    __tablename__ = "targets"
    __table_args__ = (_in("type", ("repo", "agent")),)
    id: Mapped[uuid.UUID] = _pk()
    type: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    ref: Mapped[str] = mapped_column(Text, nullable=False)
    owner_user_id: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True)
    linked_target_id: Mapped[uuid.UUID | None] = _fk("targets.id", nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    created_at: Mapped[datetime] = _created()


class Audit(Base):
    __tablename__ = "audits"
    __table_args__ = (_in("mode", ("full", "code", "agent")), _in("status", AUDIT_STATUSES))
    id: Mapped[uuid.UUID] = _pk()
    requested_by: Mapped[uuid.UUID] = _fk("users.id")
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    eval_config: Mapped[str | None] = mapped_column(Text)
    thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    current_node: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    created_at: Mapped[datetime] = _created()


class AuditTarget(Base):
    __tablename__ = "audit_targets"
    __table_args__ = (PrimaryKeyConstraint("audit_id", "target_id"),)
    audit_id: Mapped[uuid.UUID] = _fk("audits.id")
    target_id: Mapped[uuid.UUID] = _fk("targets.id")


class Finding(Base):
    __tablename__ = "findings"
    __table_args__ = (
        _in("source", ("code", "agent", "correlation")),
        _in("severity", SEVERITIES),
        _in("status", FINDING_STATUSES),
        UniqueConstraint("audit_id", "fingerprint"),
    )
    id: Mapped[uuid.UUID] = _pk()
    audit_id: Mapped[uuid.UUID] = _fk("audits.id")
    target_id: Mapped[uuid.UUID] = _fk("targets.id")
    source: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    rule_id: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float(precision=24), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'OPEN'"))
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    reasoning_summary: Mapped[str] = mapped_column(Text, nullable=False)
    tools_used: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=text("'{}'"))
    standard_refs: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=text("'{}'"))
    fix_hint: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class RiskPath(Base):
    __tablename__ = "risk_paths"
    id: Mapped[uuid.UUID] = _pk()
    audit_id: Mapped[uuid.UUID] = _fk("audits.id")
    rule_id: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    finding_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)


class ProbeRun(Base):
    __tablename__ = "probe_runs"
    id: Mapped[uuid.UUID] = _pk()
    audit_id: Mapped[uuid.UUID | None] = _fk("audits.id", nullable=True)
    target_id: Mapped[uuid.UUID] = _fk("targets.id")
    probe_id: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reply_masked: Mapped[str] = mapped_column(Text, nullable=False)
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    judged_by: Mapped[str] = mapped_column(Text, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created()


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (_in("status", ("PENDING", "APPROVED", "REJECTED")),)
    id: Mapped[uuid.UUID] = _pk()
    audit_id: Mapped[uuid.UUID] = _fk("audits.id")
    finding_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'PENDING'"))
    decided_by: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True)
    comment: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class FixProposalRow(Base):
    __tablename__ = "fix_proposals"
    __table_args__ = (
        _in("kind", ("code_patch", "manifest_patch", "prompt_patch")),
        _in("merge_status", ("PENDING", "MERGED", "CLOSED")),
    )
    id: Mapped[uuid.UUID] = _pk()
    audit_id: Mapped[uuid.UUID] = _fk("audits.id")
    finding_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    diff: Mapped[str] = mapped_column(Text, nullable=False)
    patch_check_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    approval_id: Mapped[uuid.UUID | None] = _fk("approvals.id", nullable=True)
    branch: Mapped[str | None] = mapped_column(Text)
    pr_url: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(Text, unique=True)
    merge_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'PENDING'"))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = _created()


class RiskAcceptance(Base):
    __tablename__ = "risk_acceptances"
    id: Mapped[uuid.UUID] = _pk()
    finding_id: Mapped[uuid.UUID] = _fk("findings.id")
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    accepted_by: Mapped[uuid.UUID] = _fk("users.id")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[uuid.UUID] = _pk()
    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("audits.id"), unique=True, nullable=False
    )
    report_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    report_md: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created()


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_audit_ts", "audit_id", "ts"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    audit_id: Mapped[uuid.UUID] = _fk("audits.id")
    type: Mapped[str] = mapped_column(Text, nullable=False)
    node: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TraceSpan(Base):
    __tablename__ = "trace_spans"
    __table_args__ = (_in("kind", ("node", "llm", "tool")),)
    id: Mapped[uuid.UUID] = _pk()
    audit_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    eval_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(Text)
    prompt_id: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KbDocument(Base):
    __tablename__ = "kb_documents"
    __table_args__ = (_in("source_type", KB_SOURCE_TYPES),)
    id: Mapped[uuid.UUID] = _pk()
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    ref_code: Mapped[str | None] = mapped_column(Text)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = _created()


class KbChunk(Base):
    __tablename__ = "kb_chunks"
    __table_args__ = (
        Index(
            "ix_kb_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_kb_chunks_tsv", "tsv", postgresql_using="gin"),
    )
    id: Mapped[uuid.UUID] = _pk()
    doc_id: Mapped[uuid.UUID] = _fk("kb_documents.id", ondelete="CASCADE")
    section: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    tsv: Mapped[Any] = mapped_column(TSVECTOR, Computed("to_tsvector('simple', text)", persisted=True))


class FpRule(Base):
    __tablename__ = "fp_rules"
    id: Mapped[uuid.UUID] = _pk()
    target_id: Mapped[uuid.UUID | None] = _fk("targets.id", nullable=True)
    rule_id: Mapped[str | None] = mapped_column(Text)
    fingerprint: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID] = _fk("users.id")
    created_at: Mapped[datetime] = _created()


class EvalRun(Base):
    __tablename__ = "eval_runs"
    id: Mapped[uuid.UUID] = _pk()
    requested_by: Mapped[uuid.UUID] = _fk("users.id")
    configs: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    dataset_version: Mapped[str] = mapped_column(Text, nullable=False)
    repeat: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("3"))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created()


class EvalResult(Base):
    __tablename__ = "eval_results"
    id: Mapped[uuid.UUID] = _pk()
    eval_run_id: Mapped[uuid.UUID] = _fk("eval_runs.id")
    config: Mapped[str] = mapped_column(Text, nullable=False)
    repeat_idx: Mapped[int] = mapped_column(Integer, nullable=False)
    audit_id: Mapped[uuid.UUID | None] = _fk("audits.id", nullable=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    per_case: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    latency_s: Mapped[float | None] = mapped_column(Float(precision=24))


class DlqMessage(Base):
    __tablename__ = "dlq_messages"
    __table_args__ = (_in("status", ("NEW", "REPLAYED", "IGNORED")),)
    id: Mapped[uuid.UUID] = _pk()
    original_topic: Mapped[str] = mapped_column(Text, nullable=False)
    message_key: Mapped[str | None] = mapped_column(Text)
    message: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    headers: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'NEW'"))
    created_at: Mapped[datetime] = _created()


# ---------------- Agent·Cloud 감사 (기획서 6장; DECISIONS #11~#14) ----------------
CLOUD_STATUSES = ("QUEUED", "RUNNING", "WAITING_APPROVAL", "COMPLETED", "FAILED")


class CloudTask(Base):
    """업무 상태의 기준. 증거 묶음 원본(정규화 JSON)과 최종 보고서를 보관한다."""

    __tablename__ = "cloud_tasks"
    __table_args__ = (_in("status", CLOUD_STATUSES),)
    id: Mapped[uuid.UUID] = _pk()
    requested_by: Mapped[uuid.UUID] = _fk("users.id")
    project_id: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'default'"))
    bundle_id: Mapped[str] = mapped_column(Text, nullable=False)
    bundle: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    bundle_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    model_version: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    answer: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    report_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    active_elapsed_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_event_seq: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    failure_code: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class CloudTaskEvent(Base):
    __tablename__ = "cloud_task_events"
    __table_args__ = (UniqueConstraint("task_id", "event_seq", name="uq_cloud_event_seq"),)
    id: Mapped[uuid.UUID] = _pk()
    task_id: Mapped[uuid.UUID] = _fk("cloud_tasks.id")
    event_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    ts: Mapped[datetime] = _created()


class CloudApproval(Base):
    """승인 기록. 변경 도구는 (task_id, action_id, args_hash) 가 APPROVED 인 레코드가 있어야만 실행된다."""

    __tablename__ = "cloud_approvals"
    __table_args__ = (
        UniqueConstraint("task_id", "action_id", name="uq_cloud_approval_action"),
        _in("status", ("PENDING", "APPROVED", "REJECTED")),
    )
    id: Mapped[uuid.UUID] = _pk()
    task_id: Mapped[uuid.UUID] = _fk("cloud_tasks.id")
    action_id: Mapped[str] = mapped_column(Text, nullable=False)
    tool: Mapped[str] = mapped_column(Text, nullable=False)
    args: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    args_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'PENDING'"))
    decided_by: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resume_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = _created()


class RemediationTicket(Base):
    """제품 내부 수정 요청 티켓. (task_id, action_id) 유일 — 재시도는 기존 결과를 반환한다."""

    __tablename__ = "remediation_tickets"
    __table_args__ = (UniqueConstraint("task_id", "action_id", name="uq_ticket_action"),)
    id: Mapped[uuid.UUID] = _pk()
    task_id: Mapped[uuid.UUID] = _fk("cloud_tasks.id")
    action_id: Mapped[str] = mapped_column(Text, nullable=False)
    args_hash: Mapped[str] = mapped_column(Text, nullable=False)
    approval_id: Mapped[uuid.UUID] = _fk("cloud_approvals.id")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    created_at: Mapped[datetime] = _created()
