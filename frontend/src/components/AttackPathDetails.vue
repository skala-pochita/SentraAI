<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { get } from '../api/client'
import type { Finding, PathEdge, PathRef, PathStep, ProbeEvidence, RiskPath } from '../api/types'
const emit = defineEmits<{ close: [] }>()
const props = defineProps<{ auditId: string; path: RiskPath; selection?: string | null }>()
const labels = { EXECUTION: '실행 확인', STATIC: '정적 근거', CONDITIONAL: '조건부 추정' }
const selected = ref<PathStep | PathEdge | null>(null)
const data = ref<{ findings: Finding[]; probe_runs: ProbeEvidence[] } | null>(null)
const loading = ref(false)
const error = ref('')
const titleOf = (id: string) => props.path.payload?.steps.find(s => s.id === id)?.title ?? id
const reasonLabels = { policy: '설정에서 확인한 근거', implementation_candidate: '도구 구현 후보', code_finding: '코드 검사에서 확인한 문제' }
const selectedTitle = computed(() => selected.value && ('title' in selected.value ? selected.value.title : selected.value.relation))
async function choose(item: PathStep | PathEdge) {
  selected.value = item
  if (data.value || loading.value) return
  loading.value = true; error.value = ''
  try { data.value = await get(`/audits/${props.auditId}/risk-paths/${props.path.id}/evidence`) }
  catch { error.value = '근거를 불러오지 못했습니다. 다시 선택하여 재시도할 수 있습니다.' }
  finally { loading.value = false }
}
watch(() => props.selection, id => {
  const step = props.path.payload?.steps.find(s => s.id === id)
  if (step) void choose(step)
}, { immediate: true })
function evidenceFor(r: PathRef) {
  const all = findingFor(r)?.evidence ?? []
  return r.evidence_index == null ? all : all.slice(r.evidence_index, r.evidence_index + 1)
}
function findingFor(r: PathRef) { return data.value?.findings.find(f => f.id === r.finding_id) }
function runFor(r: PathRef) { return data.value?.probe_runs.find(p => p.id === r.probe_run_id) }
</script>
<template>
  <section v-if="path.payload" class="path-detail">
    <p class="muted small">실행 환경: {{ path.payload.runtime_mode }} · {{ path.payload.observation_source === 'target_reported' ? '대상 보고 기반 관측' : '정적 분석' }}</p>
    <p v-if="path.payload.runtime_mode === 'mock'" class="small">모의 실행 기록입니다. 실제 LLM의 방어 성능이나 운영 데이터 피해를 의미하지 않습니다.</p>
    <section v-if="path.payload.code_reasons?.length" class="code-reasons" aria-label="결합 판단의 코드 및 설정 근거">
      <h4>결합 판단의 코드·설정 근거</h4>
      <p v-if="path.payload.severity_reason" class="small">{{ path.payload.severity_reason }}</p>
      <div v-for="(reason, i) in path.payload.code_reasons" :key="i" class="code-reason">
        <div class="row"><span class="badge">{{ reasonLabels[reason.kind] }}</span><b>{{ reason.title }}</b></div>
        <p class="mono small">{{ reason.ref.file_path }}:{{ reason.ref.line_start }}</p>
        <pre>{{ reason.snippet_masked }}</pre>
        <p class="small muted">{{ reason.explanation }}</p>
        <RouterLink v-if="reason.ref.finding_id" class="small" :to="`/findings/${reason.ref.finding_id}`">관련 판단 상세 보기</RouterLink>
      </div>
    </section>
    <p v-else class="muted small">이 감사에는 코드·설정 상세 조각이 수집되지 않았습니다. 아래 단계의 기존 근거를 확인할 수 있습니다.</p>
    <div class="steps" aria-label="공격 경로 단계">
      <button v-for="s in path.payload.steps" :key="s.id" class="step" :class="{ chosen: selected?.id === s.id, conditional: s.evidence_level === 'CONDITIONAL' }" :aria-pressed="selected?.id === s.id" @click="choose(s)">
        <span class="badge">{{ labels[s.evidence_level] }}</span><b>{{ s.title }}</b><span class="small">{{ s.claim }}</span>
      </button>
    </div>
    <p class="small muted">단계 사이의 관계 — 정적 연결은 실행 순서를 뜻하지 않습니다.</p>
    <div class="relations">
      <button v-for="e in path.payload.edges" :key="e.id" class="btn relation" :class="{ conditional: e.evidence_level === 'CONDITIONAL' }" :aria-pressed="selected?.id === e.id" @click="choose(e)">
        {{ titleOf(e.from_step_id) }} → {{ titleOf(e.to_step_id) }} · {{ e.relation }} <span class="badge">{{ labels[e.evidence_level] }}</span>
      </button>
    </div>
    <ul v-if="path.payload.missing_evidence.length" class="small muted"><li v-for="m in path.payload.missing_evidence" :key="m">미확인: {{ m }}</li></ul>
    <section v-if="selected" class="evidence" aria-live="polite" aria-label="선택한 단계 또는 연결의 근거">
      <div class="row"><b>{{ selectedTitle }}</b><span class="badge">{{ labels[selected.evidence_level] }}</span><button class="btn" @click="selected = null; emit('close')">닫기</button></div>
      <p v-for="c in selected.conditions" :key="c" class="small">{{ c }}</p>
      <p v-if="loading">근거를 불러오는 중입니다.</p><p v-if="error" role="alert">{{ error }}</p>
      <div v-for="(r, i) in selected.evidence_refs" :key="i" class="ref">
        <template v-if="findingFor(r)">
          <RouterLink :to="`/findings/${r.finding_id}`">{{ findingFor(r)!.title }}</RouterLink>
          <p v-if="r.field_path" class="mono small">매니페스트 필드: {{ r.field_path }}<template v-if="r.value_masked"> = {{ r.value_masked }}</template></p>
          <template v-for="(ev, n) in evidenceFor(r)" :key="n"><p v-if="ev.file_path" class="mono small">{{ ev.file_path }}:{{ ev.line_start }}</p><pre v-if="ev.snippet_masked">{{ ev.snippet_masked }}</pre></template>
        </template>
        <template v-if="runFor(r)">
          <b>{{ runFor(r)!.probe_id }} · 시도 {{ runFor(r)!.attempt }}</b>
          <p class="small">판정 {{ runFor(r)!.verdict }} · {{ runFor(r)!.judged_by }} · run {{ r.probe_run_id }}</p>
          <p class="small">공격 입력</p><pre>{{ JSON.stringify(runFor(r)!.request, null, 2) }}</pre>
          <p class="small">응답</p><pre>{{ runFor(r)!.reply_masked }}</pre>
          <p class="small">{{ r.tool_call_index === null ? '도구 호출 기록' : `선택한 도구 호출 #${r.tool_call_index + 1}` }}</p>
          <pre>{{ JSON.stringify(r.tool_call_index === null ? runFor(r)!.tool_calls : runFor(r)!.tool_calls[r.tool_call_index], null, 2) }}</pre>
        </template>
      </div>
      <p v-if="!selected.evidence_refs.length" class="muted small">직접 확인된 근거가 없는 조건부 영향입니다.</p>
    </section>
  </section>
  <p v-else class="muted small">기존 형식 · 단계 근거 미수집. 아래 Finding 링크에서 기존 결과를 확인할 수 있습니다.</p>
</template>
<style scoped>
.code-reasons { margin: 16px 0; padding: 14px; border: 1px solid var(--border); border-radius: 10px; }
.code-reason { margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border); }
.code-reason .row { flex-wrap: wrap; }
.path-detail { margin-top: 16px; }
.steps { display: grid; grid-template-columns: repeat(auto-fit,minmax(180px,1fr)); gap: 10px; margin: 12px 0; }
.step { display:flex; flex-direction:column; align-items:flex-start; gap:8px; text-align:left; padding:14px; background:var(--surface-2); color:var(--text); border:1px solid var(--border); border-radius:10px; cursor:pointer; }
.step.chosen, .step:focus-visible { outline:2px solid var(--text-2); }
.conditional { border-style:dashed; }
.relations { display:flex; flex-direction:column; align-items:stretch; gap:8px; }
.relation { white-space:normal; text-align:left; flex-wrap:wrap; justify-content:flex-start; }
.evidence { margin-top:16px; padding:16px; border:1px solid var(--border); border-radius:10px; }
.ref { margin-top:16px; padding-top:12px; border-top:1px solid var(--border); }
pre { white-space:pre-wrap; overflow-wrap:anywhere; max-height:320px; overflow:auto; }
</style>
