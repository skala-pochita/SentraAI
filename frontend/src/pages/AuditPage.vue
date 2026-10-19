<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { get, post, ApiError } from '../api/client'
import { sse } from '../api/sse'
import type { Audit, FixProposal } from '../api/types'
import { useAuth } from '../stores/auth'
import Sev from '../components/Sev.vue'

const route = useRoute()
const id = route.params.id as string
const auth = useAuth()
const audit = ref<Audit | null>(null)
const events = ref<{ type: string; node?: string; payload: Record<string, unknown>; ts?: string }[]>([])
const fixes = ref<FixProposal[]>([])
const error = ref('')
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
        try { const d = JSON.parse(m.data); events.value.push({ type: d.type ?? m.event, node: d.node, payload: d.payload ?? {}, ts: d.ts }) } catch { /* ping */ }
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
const lines = (d: string) => d.split('\n').map((t) => ({ t, c: t.startsWith('+') ? 'add' : t.startsWith('-') ? 'del' : '' }))
const sevs = computed(() => Object.entries(audit.value?.summary?.by_severity ?? {}))
</script>

<template>
  <p v-if="error" class="error">{{ error }}</p>
  <div v-if="audit" class="grid cols-2">
    <div class="card">
      <div class="row"><h3>감사 {{ id.slice(0, 8) }}</h3><span class="status" :class="audit.status">{{ audit.status }}</span></div>
      <progress :value="audit.progress" max="1" style="width:100%" />
      <p class="muted">현재 노드: {{ audit.current_node ?? '-' }}</p>
      <div class="row"><Sev v-for="[k, v] in sevs" :key="k" :v="k" /> <span v-if="!sevs.length" class="muted">결과 대기</span></div>
      <div v-if="audit.status === 'WAITING_APPROVAL'" class="card mt hold">
        <b>CRITICAL 수정 승인 요청</b>
        <div class="row mt"><button class="btn primary" :disabled="!auth.isAdmin" @click="decide('APPROVE')">승인</button>
          <button class="btn danger" :disabled="!auth.isAdmin" @click="decide('REJECT')">반려</button>
          <span v-if="!auth.isAdmin" class="muted">ADMIN 만 결정할 수 있습니다.</span></div>
      </div>
      <div v-if="done" class="row mt"><RouterLink class="btn" :to="`/audits/${id}/findings`">Findings</RouterLink>
        <RouterLink class="btn" :to="`/audits/${id}/risks`">결합 위험</RouterLink><RouterLink class="btn" :to="`/audits/${id}/redteam`">레드팀</RouterLink>
        <RouterLink class="btn" :to="`/audits/${id}/report`">보고서</RouterLink></div>
    </div>
    <div class="card"><h3>진행 이벤트 (SSE)</h3><div class="log">
      <div v-for="(e, i) in events" :key="i"><b>{{ e.type }}</b> <span class="muted">{{ e.node }}</span> {{ JSON.stringify(e.payload).slice(0, 140) }}</div></div></div>
  </div>
  <div v-if="fixes.length" class="card mt"><h3>수정·PR</h3>
    <div v-for="f in fixes" :key="f.id" class="mt">
      <div class="row"><b>{{ f.kind }}</b><span class="badge">{{ f.merge_status ?? 'NOT_MERGED' }}</span><span class="muted">{{ f.branch }}</span>
        <span class="badge">{{ f.patch_check_passed ? '패치 검사 통과' : '패치 검사 실패' }}</span>
        <button class="btn" v-if="audit?.status === 'WAITING_MERGE' && f.merge_status !== 'MERGED'" @click="merge(f)">병합</button></div>
      <pre class="diff"><div v-for="(l, i) in lines(f.diff)" :key="i" :class="l.c">{{ l.t }}</div></pre></div>
  </div>
</template>
