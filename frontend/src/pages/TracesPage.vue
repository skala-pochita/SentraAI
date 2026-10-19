<script setup lang="ts">
import { computed, ref } from 'vue'
import { get } from '../api/client'
interface Span { id: string; kind: string; name: string; model: string | null; prompt_id: string | null; input_tokens: number; output_tokens: number; cost_usd: number; latency_ms: number; status: string }
const audit = ref('')
const rows = ref<Span[]>([])
const error = ref('')
async function load() { try { rows.value = await get<Span[]>(`/traces?audit_id=${audit.value}`); error.value = '' } catch (e) { error.value = (e as Error).message } }
const cost = computed(() => rows.value.reduce((a, s) => a + s.cost_usd, 0))
const tokens = computed(() => rows.value.reduce((a, s) => a + s.input_tokens + s.output_tokens, 0))
</script>
<template>
  <div class="card"><div class="row"><input v-model="audit" placeholder="audit_id" style="width:340px" /><button class="btn primary" @click="load">조회</button>
    <span class="muted">span {{ rows.length }} · 토큰 {{ tokens }} · ${{ cost.toFixed(4) }}</span></div><p v-if="error" class="error">{{ error }}</p>
    <table class="mt"><tr><th>종류</th><th>이름</th><th>모델/프롬프트</th><th>토큰</th><th>지연</th><th>상태</th></tr>
      <tr v-for="s in rows" :key="s.id"><td>{{ s.kind }}</td><td>{{ s.name }}</td><td>{{ s.model }} {{ s.prompt_id }}</td>
        <td>{{ s.input_tokens }}/{{ s.output_tokens }}</td><td>{{ s.latency_ms }}ms</td><td>{{ s.status }}</td></tr></table></div>
</template>
