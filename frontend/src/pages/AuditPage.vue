<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { get } from '../api/client'
import { sse } from '../api/sse'
import type { Audit } from '../api/types'
import Sev from '../components/Sev.vue'
import Icon from '../components/Icon.vue'
import AuditTabs from '../components/AuditTabs.vue'
import type { Shot } from '../three/stage'
// three.js 는 probe 이벤트가 처음 도착할 때만 내려받는다
const ProbeArena = defineAsyncComponent(() => import('../components/ProbeArena.vue'))

const route = useRoute()
const id = route.params.id as string
const audit = ref<Audit | null>(null)
const events = ref<{ type: string; node?: string; payload: Record<string, unknown>; ts?: string }[]>([])
const error = ref('')
const logEl = ref<HTMLElement | null>(null)
const follow = ref(true)
const ctl = new AbortController()
const shots = ref<Shot[]>([])

// probe.progress 는 probe 하나의 시도 수·성공 수만 알려 주므로 그만큼 발사한다 (적응형 probe 는 기본 probe 발사대에서)
function onProbe(p: Record<string, unknown>) {
  if (typeof p.probe_id !== 'string') return
  const lane = p.probe_id.replace(/-A\d+$/, '')
  const n = Number(p.attempts) || 0
  const hit = Number(p.successes) || 0
  for (let i = 0; i < n; i++) shots.value.push({ lane, success: i < hit })
}

const done = computed(() => audit.value && ['COMPLETED', 'FAILED', 'CANCELLED'].includes(audit.value.status))
async function refresh() {
  try { audit.value = await get<Audit>(`/audits/${id}`); error.value = '' }
  catch { error.value = '검사 상태를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.' }
}
async function subscribe() {
  // 연결이 끊기면 상태 조회로 복구한 뒤 재구독한다
  while (!ctl.signal.aborted) {
    try {
      await sse(`/api/v1/audits/${id}/events`, (m) => {
        try {
          const d = JSON.parse(m.data); events.value.push({ type: d.type ?? m.event, node: d.node, payload: d.payload ?? {}, ts: d.ts })
          if ((d.type ?? m.event) === 'probe.progress') onProbe(d.payload ?? {})
          if (follow.value) void nextTick(() => logEl.value?.scrollTo({ top: logEl.value.scrollHeight }))
        } catch { /* ping */ }
        if (m.event.startsWith('audit.') || m.event === 'node.finished' || m.event === 'node.started') void refresh()
      }, ctl.signal)
    } catch { if (ctl.signal.aborted) return }
    await refresh()
    if (done.value) return
    await new Promise((r) => setTimeout(r, 2000))
  }
}
onMounted(async () => { await refresh(); void subscribe() })
onBeforeUnmount(() => ctl.abort())

const ORDER = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO']
const sevs = computed(() => {
  const s = audit.value?.summary?.by_severity ?? {}
  return [...ORDER.filter((k) => k in s), ...Object.keys(s).filter((k) => !ORDER.includes(k))].map((k) => [k, s[k]] as const)
})
const statusNames: Record<string, string> = { QUEUED: '검사 대기 중', RUNNING: '보안 검사 진행 중', COMPLETED: '보안 검사 완료', FAILED: '검사가 중단되었습니다', CANCELLED: '검사가 취소되었습니다', WAITING_APPROVAL: '승인 대기 중' }
const modeNames: Record<string, string> = { full: '코드 + AI 에이전트 검사', code: '코드 보안 검사', agent: 'AI 에이전트 공격 검증' }
const stages = computed(() => [
  { key: 'intake', label: '프로젝트 준비' },
  ...(audit.value?.mode !== 'agent' ? [{ key: 'code', label: '코드 분석' }] : []),
  ...(audit.value?.mode !== 'code' ? [{ key: 'agent', label: '공격 검증' }] : []),
  { key: 'correlate', label: '위험 분석' }, { key: 'report', label: '보고서 작성' },
])
const activeStage = computed(() => {
  const node = audit.value?.current_node ?? ''
  if (node.startsWith('code')) return 'code'
  if (node.startsWith('agent') || node.startsWith('probe') || node === 'prepare_sandbox') return 'agent'
  return node
})
const currentWork = computed(() => {
  if (audit.value?.status !== 'RUNNING') return statusNames[audit.value?.status ?? ''] ?? '검사 상태 확인 중'
  return stages.value.find(s => s.key === activeStage.value)?.label.concat(' 중') ?? '검사 결과 처리 중'
})
const pct = computed(() => Math.round((audit.value?.progress ?? 0) * 100))
const evClass = (t: string) => t.split('.')[0]
const time = (ts?: string) => (ts ? ts.slice(11, 19) : '')
const brief = (e: { payload: Record<string, unknown> }) => {
  const p = e.payload
  if (typeof p.tool === 'string') return { k: p.tool, v: '도구 실행' }
  return { k: '', v: '처리 중' }
}
function onScroll() {
  const el = logEl.value
  if (el) follow.value = el.scrollHeight - el.scrollTop - el.clientHeight < 40
}
</script>

<template>
  <AuditTabs :id="id" :completed="audit?.status === 'COMPLETED'" />
  <p v-if="error" class="error"><Icon name="alert" :size="16" />{{ error }}</p>
  <section v-if="audit" class="card demo-summary mb" aria-live="polite">
    <span class="badge">{{ modeNames[audit.mode] || audit.mode }}</span>
    <p class="muted">{{ audit.name || '프로젝트 보안 검사' }}</p>
    <h2>{{ audit.status === 'COMPLETED' ? '보안 검사 완료' : currentWork }}</h2>
    <template v-if="audit.status === 'COMPLETED'">
      <div class="demo-metrics">
        <div><span>발견된 보안 문제</span><strong>{{ audit.summary?.total ?? '—' }}<small>건</small></strong></div>
        <div><span>긴급 · CRITICAL</span><strong class="critical">{{ audit.summary?.by_severity?.CRITICAL ?? 0 }}<small>건</small></strong></div>
      </div>
      <RouterLink class="btn primary lg" :to="`/audits/${id}/findings`">검사 결과 확인<Icon name="chevron" :size="18" /></RouterLink>
    </template>
    <template v-else-if="!done">
      <p class="muted">현재 진행 중인 작업을 강조해서 표시합니다.</p>
      <ol class="demo-stages"><li v-for="stage in stages" :key="stage.key" :class="{ active: audit.status === 'RUNNING' && activeStage === stage.key }" :aria-current="audit.status === 'RUNNING' && activeStage === stage.key ? 'step' : undefined">{{ stage.label }}</li></ol>
    </template>
    <p v-else class="muted">검사가 완료되지 않았습니다. 실행 기록을 확인한 뒤 다시 검사를 시작해 주세요. <RouterLink to="/audits/new">새 검사 시작</RouterLink></p>
  </section>
  <div v-if="audit" class="stack" style="gap:16px">
    <div class="stack" style="gap:16px">
      <div class="card">
        <div class="card-head">
          <h3><Icon name="shield" :size="16" />{{ audit.name || '보안 감사 진행' }}</h3>
          <span class="status" :class="audit.status">{{ statusNames[audit.status] || audit.status }}</span>
          <span class="spacer" />
          <span class="badge">{{ modeNames[audit.mode] || audit.mode }}</span>
        </div>
        <div class="row" style="justify-content:space-between; margin-bottom:8px">
          <span class="muted small">진행률</span><span class="num strong">{{ pct }}%</span>
        </div>
        <div class="progress" :class="{ active: !done }"><span :style="{ width: `${pct}%` }" /></div>
        <div class="grid cols-2 mt">
          <div class="kv"><span class="muted small">현재 작업</span><span class="mono">{{ currentWork }}</span></div>
          <div class="kv"><span class="muted small">시작</span><span class="mono">{{ audit.started_at ? new Date(audit.started_at).toLocaleString('ko-KR') : '시작 대기' }}</span></div>
        </div>
        <p class="section-title mt">심각도</p>
        <div class="chips"><Sev v-for="[k, v] in sevs" :key="k" :v="k" :n="v" /><span v-if="!sevs.length" class="muted small">결과 대기</span></div>
      </div>

      <div v-if="shots.length" class="card">
        <div class="card-head"><h3><Icon name="crosshair" :size="16" />모의 공격 실행 현황</h3>
          <span class="badge tone" :class="done ? '' : 'ok'"><span v-if="!done" class="live" />{{ done ? '종료' : '실시간' }}</span></div>
        <ProbeArena :feed="shots" :height="320" />
      </div>

      <div v-if="audit.status === 'COMPLETED'" class="grid cols-2">
        <RouterLink class="card hover tile" :to="`/audits/${id}/findings`"><Icon name="bug" /><b>발견된 보안 문제</b><span class="muted small">탐지 결과 {{ audit.summary?.total ?? 0 }}건</span></RouterLink>
        <RouterLink class="card hover tile" :to="`/audits/${id}/risks`"><Icon name="share" /><b>연결된 공격 경로</b><span class="muted small">보안 문제가 연결되어 생기는 공격 시나리오</span></RouterLink>
        <RouterLink class="card hover tile" :to="`/audits/${id}/redteam`"><Icon name="crosshair" /><b>모의 공격 검증</b><span class="muted small">공격 시도와 방어 성공 여부</span></RouterLink>
        <RouterLink class="card hover tile" :to="`/audits/${id}/report`"><Icon name="file" /><b>보고서</b><span class="muted small">결과 요약과 권장 조치</span></RouterLink>
      </div>
    </div>

    <details class="card">
      <summary>상세 실행 기록 · {{ events.length }}건</summary>
      <div class="card-head mt">
        <h3><Icon name="terminal" :size="16" />진행 이벤트</h3>
        <span class="badge tone" :class="done ? '' : 'ok'"><span v-if="!done" class="live" />{{ done ? '종료' : '진행 기록' }}</span>
        <span class="spacer" /><span class="muted small num">{{ events.length }} events</span>
      </div>
      <div ref="logEl" class="log" @scroll="onScroll">
        <div v-for="(e, i) in events" :key="i">
          <span class="t">{{ time(e.ts) }}</span>
          <span class="ev" :class="evClass(e.type)" :title="e.type">{{ e.type }}</span>
          <span class="p"><template v-if="e.node">{{ e.node }} · </template><b v-if="brief(e).k">{{ brief(e).k }} </b>{{ brief(e).v }}</span>
        </div>
        <div v-if="!events.length"><span class="t" /><span class="ev">waiting</span><span class="p">이벤트를 기다리는 중…</span></div>
      </div>
    </details>
  </div>

</template>

<style scoped>
.demo-summary { border-top: 4px solid var(--accent); }
.demo-summary h2 { font-size: clamp(26px, 4vw, 38px); margin: 12px 0 24px; }
.demo-metrics { display:flex; flex-wrap:wrap; gap:40px; margin-bottom:24px; }
.demo-metrics span { display:block; color:var(--text-2); }
.demo-metrics strong { display:block; font-size:44px; }
.demo-metrics small { font-size:18px; margin-left:8px; }
.critical { color:var(--critical); }
.demo-stages { display:flex; flex-wrap:wrap; gap:12px; padding:0; list-style:none; }
.demo-stages li { padding:14px 20px; border:1px solid var(--border); border-radius:12px; color:var(--muted); }
.demo-stages li.active { background:var(--accent-soft); color:var(--text); border-color:var(--accent); font-weight:700; }
summary { cursor:pointer; font-weight:600; }

.kv { display: flex; flex-direction: column; gap: 3px; padding: 10px 12px; border-radius: 10px; background: var(--surface-2); border: 1px solid var(--border); min-width: 0; }
.kv .mono { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text); }
.tile { display: flex !important; flex-direction: column; gap: 4px; padding: 16px; }
.tile .icon { color: var(--accent); margin-bottom: 6px; }
.tile b { color: var(--text); }
.fix { margin-top: 12px; }
.fix > summary { list-style: none; cursor: pointer; flex-wrap: wrap; border-radius: 12px; }
.fix > summary::-webkit-details-marker { display: none; }
.fix[open] > summary { border-radius: 12px 12px 0 0; }
.fix:not([open]) > summary { border-bottom: 1px solid #1d2631; }
.fix .caret { transition: transform .2s var(--ease); color: #7d8a99; }
.fix[open] .caret { transform: rotate(90deg); }
.fix > pre { border-top-left-radius: 0; border-top-right-radius: 0; max-height: 560px; }
</style>
