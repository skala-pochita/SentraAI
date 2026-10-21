<script setup lang="ts">
import { computed, ref } from 'vue'
import { get } from '../api/client'
import Icon from '../components/Icon.vue'
import Stat from '../components/Stat.vue'
interface Span { id: string; kind: string; name: string; model: string | null; prompt_id: string | null; input_tokens: number; output_tokens: number; cost_usd: number; latency_ms: number; status: string }
const audit = ref('')
const rows = ref<Span[]>([])
const error = ref('')
const searched = ref(false)
const kind = ref('')
async function load() {
  try { rows.value = await get<Span[]>(`/traces?audit_id=${audit.value}`); error.value = '' } catch (e) { error.value = (e as Error).message }
  searched.value = true
}
const cost = computed(() => rows.value.reduce((a, s) => a + s.cost_usd, 0))
const tokens = computed(() => rows.value.reduce((a, s) => a + s.input_tokens + s.output_tokens, 0))
const kinds = computed(() => [...new Set(rows.value.map((s) => s.kind))])
const shown = computed(() => rows.value.filter((s) => !kind.value || s.kind === kind.value))
const maxLat = computed(() => Math.max(1, ...rows.value.map((s) => s.latency_ms)))
const KIND_TONE: Record<string, string> = { node: 'violet', llm: 'ok', tool: 'info' }
</script>
<template>
  <div class="card mb">
    <form class="row" @submit.prevent="load">
      <span class="input-icon" style="flex:1; min-width:260px; max-width:440px"><Icon name="search" :size="16" /><input v-model="audit" class="mono" placeholder="audit_id 를 붙여 넣으세요" aria-label="audit_id" /></span>
      <button class="btn primary" type="submit"><Icon name="activity" :size="15" />조회</button>
    </form>
    <p v-if="error" class="error mt"><Icon name="alert" :size="16" />{{ error }}</p>
  </div>
  <div class="grid cols-3 mb">
    <Stat label="span" :value="rows.length.toLocaleString()" icon="layers" color="var(--violet)" />
    <Stat label="토큰" :value="tokens.toLocaleString()" icon="cpu" color="var(--accent)" />
    <Stat label="비용" :value="`$${cost.toFixed(4)}`" icon="coins" color="var(--warn)" />
  </div>
  <div class="card flush">
    <div v-if="rows.length" class="card-head">
      <div class="chips"><button class="chip-btn" :class="{ on: !kind }" @click="kind = ''">전체</button>
        <button v-for="k in kinds" :key="k" class="chip-btn" :class="{ on: kind === k }" @click="kind = k">{{ k }}</button></div>
    </div>
    <div v-if="shown.length" class="table-wrap"><table>
      <thead><tr><th>종류</th><th>이름</th><th>모델/프롬프트</th><th class="r">토큰 in/out</th><th>지연</th><th>상태</th></tr></thead>
      <tbody><tr v-for="s in shown" :key="s.id">
        <td><span class="badge tone mono" :class="KIND_TONE[s.kind] ?? ''">{{ s.kind }}</span></td>
        <td class="mono" style="color:var(--text)">{{ s.name }}</td>
        <td class="small"><span v-if="s.model" class="mono">{{ s.model }}</span> <span v-if="s.prompt_id" class="muted mono">{{ s.prompt_id }}</span></td>
        <td class="r num">{{ s.input_tokens }}/{{ s.output_tokens }}</td>
        <td><div class="meter"><div class="progress"><span :style="{ width: `${(s.latency_ms / maxLat) * 100}%` }" /></div><span class="v" style="width:64px">{{ s.latency_ms }}ms</span></div></td>
        <td><span class="status" :class="s.status === 'ok' ? 'ok' : 'bad'">{{ s.status }}</span></td>
      </tr></tbody>
    </table></div>
    <div v-else class="empty"><Icon name="activity" /><b>{{ searched ? 'span 이 없습니다' : 'audit_id 로 조회하세요' }}</b><span>감사 1건의 node·llm·tool span 이 trace_id 하나로 묶여 표시됩니다.</span></div>
  </div>
</template>
