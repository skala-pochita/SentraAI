<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, ApiError } from '../api/client'
import { sse } from '../api/sse'
import type { CloudEvent, CloudSource, CloudTask } from '../api/types'
import { useAuth } from '../stores/auth'
import Sev from '../components/Sev.vue'

const id = useRoute().params.id as string
const auth = useAuth()
const task = ref<CloudTask | null>(null)
const events = ref<CloudEvent[]>([])
const tokenText = ref('')
const source = ref<CloudSource | null>(null)
const error = ref('')
const ctl = new AbortController()
let lastSeq = 0

const terminal = computed(() => task.value && ['COMPLETED', 'FAILED'].includes(task.value.status))
const pa = computed(() => task.value?.answer?.pendingAction ?? null)
const refresh = async () => { task.value = await api<CloudTask>(`/../ai/tasks/${id}`) }

async function subscribe() {
  // 끊기면 작업 조회로 현재 결과·승인 대기를 복구한 뒤 마지막 eventSeq 이후만 재구독한다 (과거 토큰 재전송 없음)
  while (!ctl.signal.aborted) {
    try {
      await sse(`/api/ai/tasks/${id}/stream?after=${lastSeq}`, (m) => {
        const e = JSON.parse(m.data) as CloudEvent
        lastSeq = Math.max(lastSeq, e.eventSeq); events.value.push(e)
        if (e.type === 'TOKEN') tokenText.value += String(e.data.text ?? '')
        if (['APPROVAL_REQUIRED', 'DONE', 'FAILED'].includes(e.type)) void refresh()
      }, ctl.signal)
    } catch { if (ctl.signal.aborted) return }
    await refresh()
    if (terminal.value) return
    await new Promise((r) => setTimeout(r, 1500))
  }
}
onMounted(async () => { await refresh(); void subscribe() })
onBeforeUnmount(() => ctl.abort())

async function decide(decision: 'APPROVE' | 'REJECT') {
  if (!pa.value) return
  try {
    await api(`/../ai/tasks/${id}/approval`, { method: 'POST', body: JSON.stringify({ actionId: pa.value.actionId, decision, argsHash: pa.value.argsHash }) })
    error.value = ''; setTimeout(refresh, 800)
  } catch (e) { error.value = e instanceof ApiError ? e.body.message : String(e) }
}
const sourceOf = (eid: string) => task.value?.sources.find((s) => s.evidence_id === eid)
const toolEvents = computed(() => events.value.filter((e) => e.type.startsWith('TOOL')))
</script>

<template>
  <p v-if="error" class="error">{{ error }}</p>
  <div v-if="task" class="grid cols-2">
    <div class="card">
      <div class="row"><h3>작업 {{ id.slice(0, 8) }}</h3><span class="status" :class="task.status">{{ task.status }}</span>
        <span v-if="task.answer?.llmUsed === false" class="badge" title="LLM 없이 결정론 점검만 수행">규칙 모드</span></div>
      <p v-if="task.failureCode" class="error">실패 코드: {{ task.failureCode }}{{ task.answer?.partial ? ' (부분 결과)' : '' }}</p>
      <p v-if="tokenText || task.answer?.summary">{{ task.answer?.summary || tokenText }}</p>
      <div v-if="pa" class="card hold mt"><b>승인 필요: {{ pa.tool }}</b>
        <table><tr><th>대상</th><td>{{ pa.target }}</td></tr><tr><th>제목</th><td>{{ pa.args.title }}</td></tr><tr><th>내용</th><td><pre>{{ pa.args.body }}</pre></td></tr>
          <tr><th>인수 해시</th><td class="muted" style="word-break:break-all">{{ pa.argsHash }}</td></tr></table>
        <div class="row"><button class="btn primary" :disabled="!auth.isAdmin" @click="decide('APPROVE')">승인</button>
          <button class="btn danger" :disabled="!auth.isAdmin" @click="decide('REJECT')">거부</button><span v-if="!auth.isAdmin" class="muted">승인자(ADMIN)만 결정할 수 있습니다.</span></div></div>
      <p v-if="task.answer?.actionResult" class="mt">후속 조치: <b>{{ task.answer.actionResult.status }}</b> {{ task.answer.actionResult.ticket_id }}</p>
    </div>
    <div class="card"><h3>도구 호출 (실제 MCP 도구 이름)</h3><div class="log">
      <div v-for="e in toolEvents" :key="e.eventSeq"><b>{{ e.type }}</b> {{ e.data.tool }} <span class="muted">{{ e.type === 'TOOL_START' ? JSON.stringify(e.data.args).slice(0, 100) : (e.data.error ? '오류 ' + e.data.code : '완료') }}</span></div>
      <div v-for="e in events.filter(x => x.type === 'APPROVAL_REQUIRED' || x.type === 'DONE' || x.type === 'FAILED')" :key="'s' + e.eventSeq"><b>{{ e.type }}</b></div></div></div>
  </div>
  <div v-if="task?.answer" class="card mt"><h3>발견사항 ({{ task.answer.findings.length }})</h3>
    <p v-if="!task.answer.findings.length" class="muted">확인된 결함이 없습니다.</p>
    <div v-for="(f, i) in task.answer.findings" :key="i" class="mt" :class="{ hold: f.status === 'HOLD' }">
      <div class="row"><Sev :v="f.severity" /><b>{{ f.area }} · {{ f.category }}</b><span class="badge">{{ f.status === 'CONFIRMED' ? '결함 확인' : f.status === 'CONDITIONAL' ? '조건부 위험' : '판단 보류' }}</span><span class="muted">{{ f.resource_id }}</span></div>
      <p>{{ f.reason }}</p>
      <p v-if="f.path.length" class="muted">경로: {{ f.path.join(' → ') }}</p>
      <ul v-if="f.conditions.length"><li v-for="c in f.conditions" :key="c">성립 조건: {{ c }}</li></ul>
      <p v-if="f.needs.length" class="error">추가로 필요한 증거: {{ f.needs.join(', ') }}</p>
      <p v-if="f.fix_hint">개선: {{ f.fix_hint }}</p>
      <p class="muted">근거: <a v-for="e in f.evidence_ids" :key="e" href="#" @click.prevent="source = sourceOf(e) ?? { evidence_id: e, kind: 'other' }">{{ e }} </a></p></div>
    <div v-if="task.answer.normal.length" class="mt"><b>정상 판정</b><ul><li v-for="n in task.answer.normal" :key="n">{{ n }}</li></ul></div>
    <div v-if="task.answer.notChecked.length" class="mt"><b>미점검</b><ul><li v-for="n in task.answer.notChecked" :key="n">{{ n }}</li></ul></div>
  </div>
  <div v-if="source" class="card mt"><b>근거 원문 · {{ source.evidence_id }}</b> <span class="muted">{{ source.kind }} {{ source.location }} {{ source.uri }} {{ source.version }}</span>
    <pre>{{ JSON.stringify(source.excerpt ?? {}, null, 1) }}</pre><button class="btn" @click="source = null">닫기</button></div>
</template>
