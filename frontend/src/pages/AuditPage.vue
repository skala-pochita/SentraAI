<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { get, post, ApiError } from '../api/client'
import { sse } from '../api/sse'
import type { Audit, FixProposal } from '../api/types'
import { useAuth } from '../stores/auth'
import Sev from '../components/Sev.vue'
import Icon from '../components/Icon.vue'
import AuditTabs from '../components/AuditTabs.vue'

const route = useRoute()
const id = route.params.id as string
const auth = useAuth()
const audit = ref<Audit | null>(null)
const events = ref<{ type: string; node?: string; payload: Record<string, unknown>; ts?: string }[]>([])
const fixes = ref<FixProposal[]>([])
const error = ref('')
const logEl = ref<HTMLElement | null>(null)
const follow = ref(true)
const ctl = new AbortController()

const done = computed(() => audit.value && ['COMPLETED', 'FAILED', 'CANCELLED'].includes(audit.value.status))
async function refresh() {
  audit.value = await get<Audit>(`/audits/${id}`)
  fixes.value = await get<FixProposal[]>(`/audits/${id}/fixes`)
}
async function subscribe() {
  // 연결이 끊기면 상태 조회로 복구한 뒤 재구독한다
  while (!ctl.signal.aborted) {
    try {
      await sse(`/api/v1/audits/${id}/events`, (m) => {
        try {
          const d = JSON.parse(m.data); events.value.push({ type: d.type ?? m.event, node: d.node, payload: d.payload ?? {}, ts: d.ts })
          if (follow.value) void nextTick(() => logEl.value?.scrollTo({ top: logEl.value.scrollHeight }))
        } catch { /* ping */ }
        if (m.event.startsWith('audit.') || m.event === 'node.finished') void refresh()
      }, ctl.signal)
    } catch { if (ctl.signal.aborted) return }
    await refresh()
    if (done.value) return
    await new Promise((r) => setTimeout(r, 2000))
  }
}
onMounted(async () => { await refresh(); void subscribe() })
onBeforeUnmount(() => ctl.abort())

async function decide(decision: 'APPROVE' | 'REJECT') {
  try { await post(`/approvals/${audit.value?.pending_approval_id}/decision`, { decision, comment: '' }); await refresh() }
  catch (e) { error.value = e instanceof ApiError ? e.body.message : String(e) }
}
async function merge(f: FixProposal) {
  try { await post(`/fixes/${f.id}/merge`); await refresh() } catch (e) { error.value = e instanceof ApiError ? e.body.message : String(e) }
}
const lines = (d: string) => d.split('\n').map((t) => ({ t, c: t.startsWith('@@') ? 'hunk' : t.startsWith('+') ? 'add' : t.startsWith('-') ? 'del' : '' }))
const stat = (d: string) => {
  const ls = d.split('\n')
  return { add: ls.filter((l) => l.startsWith('+') && !l.startsWith('+++')).length, del: ls.filter((l) => l.startsWith('-') && !l.startsWith('---')).length }
}
const ORDER = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO']
const sevs = computed(() => {
  const s = audit.value?.summary?.by_severity ?? {}
  return [...ORDER.filter((k) => k in s), ...Object.keys(s).filter((k) => !ORDER.includes(k))].map((k) => [k, s[k]] as const)
})
const pct = computed(() => Math.round((audit.value?.progress ?? 0) * 100))
const evClass = (t: string) => t.split('.')[0]
const time = (ts?: string) => (ts ? ts.slice(11, 19) : '')
const brief = (e: { payload: Record<string, unknown> }) => {
  const p = e.payload
  if (typeof p.why === 'string') return { k: String(p.tool ?? ''), v: p.why }
  return { k: '', v: JSON.stringify(p).slice(0, 160) }
}
function onScroll() {
  const el = logEl.value
  if (el) follow.value = el.scrollHeight - el.scrollTop - el.clientHeight < 40
}
</script>

<template>
  <AuditTabs :id="id" />
  <p v-if="error" class="error"><Icon name="alert" :size="16" />{{ error }}</p>
  <div v-if="audit" class="grid split">
    <div class="stack" style="gap:16px">
      <div class="card">
        <div class="card-head">
          <h3><Icon name="shield" :size="16" />감사 <span class="mono">{{ id.slice(0, 8) }}</span></h3>
          <span class="status" :class="audit.status">{{ audit.status }}</span>
          <span class="spacer" />
          <span class="badge">{{ audit.mode }}</span>
        </div>
        <div class="row" style="justify-content:space-between; margin-bottom:8px">
          <span class="muted small">진행률</span><span class="num strong">{{ pct }}%</span>
        </div>
        <div class="progress" :class="{ active: !done }"><span :style="{ width: `${pct}%` }" /></div>
        <div class="grid cols-2 mt">
          <div class="kv"><span class="muted small">현재 노드</span><span class="mono">{{ audit.current_node ?? '-' }}</span></div>
          <div class="kv"><span class="muted small">시작</span><span class="mono">{{ audit.created_at?.slice(0, 19).replace('T', ' ') }}</span></div>
        </div>
        <p class="section-title mt">심각도</p>
        <div class="chips"><Sev v-for="[k, v] in sevs" :key="k" :v="k" :n="v" /><span v-if="!sevs.length" class="muted small">결과 대기</span></div>
      </div>

      <div v-if="audit.status === 'WAITING_APPROVAL'" class="callout">
        <Icon name="alert" :size="20" />
        <div style="flex:1">
          <b>CRITICAL 수정 승인 요청</b>
          <p class="muted small" style="margin:2px 0 12px">승인하면 수정안이 브랜치로 적용되고 PR 이 만들어집니다.</p>
          <div class="row"><button class="btn primary" :disabled="!auth.isAdmin" @click="decide('APPROVE')"><Icon name="check" :size="15" />승인</button>
            <button class="btn danger" :disabled="!auth.isAdmin" @click="decide('REJECT')"><Icon name="x" :size="15" />반려</button>
            <span v-if="!auth.isAdmin" class="muted small">ADMIN 만 결정할 수 있습니다.</span></div>
        </div>
      </div>

      <div v-if="done" class="grid cols-2">
        <RouterLink class="card hover tile" :to="`/audits/${id}/findings`"><Icon name="bug" /><b>Findings</b><span class="muted small">탐지 결과 {{ audit.summary?.total ?? 0 }}건</span></RouterLink>
        <RouterLink class="card hover tile" :to="`/audits/${id}/risks`"><Icon name="share" /><b>결합 위험</b><span class="muted small">위험 경로</span></RouterLink>
        <RouterLink class="card hover tile" :to="`/audits/${id}/redteam`"><Icon name="crosshair" /><b>레드팀</b><span class="muted small">probe 결과·ASR</span></RouterLink>
        <RouterLink class="card hover tile" :to="`/audits/${id}/report`"><Icon name="file" /><b>보고서</b><span class="muted small">Markdown 보고서</span></RouterLink>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <h3><Icon name="terminal" :size="16" />진행 이벤트</h3>
        <span class="badge tone" :class="done ? '' : 'ok'"><span v-if="!done" class="live" />{{ done ? '종료' : 'SSE 연결' }}</span>
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
    </div>
  </div>

  <div v-if="fixes.length" class="card mt">
    <div class="card-head"><h3><Icon name="branch" :size="16" />수정 · PR</h3><span class="sub">{{ fixes.length }}개 수정안</span></div>
    <details v-for="(f, fi) in fixes" :key="f.id" class="fix" :open="fi === 0">
      <summary class="code-head">
        <Icon name="chevron" :size="14" class="caret" />
        <b style="color:#e6edf3">{{ f.kind }}</b><span>{{ f.branch }}</span>
        <span class="spacer" />
        <span class="badge tone" :class="f.patch_check_passed ? 'ok' : 'bad'"><Icon :name="f.patch_check_passed ? 'check' : 'x'" />{{ f.patch_check_passed ? '패치 검사 통과' : '패치 검사 실패' }}</span>
        <span class="badge tone" :class="f.merge_status === 'MERGED' ? 'ok' : 'warn'"><Icon name="merge" />{{ f.merge_status ?? 'NOT_MERGED' }}</span>
        <button v-if="audit?.status === 'WAITING_MERGE' && f.merge_status !== 'MERGED'" class="btn primary sm" @click="merge(f)"><Icon name="merge" :size="14" />병합</button>
        <span class="muted small num">+{{ stat(f.diff).add }} −{{ stat(f.diff).del }}</span>
      </summary>
      <pre class="diff"><div v-for="(l, i) in lines(f.diff)" :key="i" :class="l.c">{{ l.t }}</div></pre>
    </details>
  </div>
</template>

<style scoped>
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
