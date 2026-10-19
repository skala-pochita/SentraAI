<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { get, patch, ApiError } from '../api/client'
import type { Finding } from '../api/types'
import { useAuth } from '../stores/auth'
import Sev from '../components/Sev.vue'
const id = useRoute().params.id as string
const f = ref<Finding | null>(null)
const reason = ref('')
const error = ref('')
const auth = useAuth()
const load = async () => { f.value = await get<Finding>(`/findings/${id}`) }
onMounted(load)
async function falsePositive() {
  try { await patch(`/findings/${id}`, { false_positive: { reason: reason.value } }); await load() }
  catch (e) { error.value = e instanceof ApiError ? e.body.message : String(e) }
}
</script>
<template>
  <div v-if="f" class="grid cols-2">
    <div class="card"><div class="row"><Sev :v="f.severity" /><h3>{{ f.title }}</h3></div>
      <p>{{ f.reasoning_summary }}</p>
      <p class="muted">카테고리 {{ f.category }} · 규칙 {{ f.rule_id }} · 신뢰도 {{ f.confidence }} · 상태 {{ f.status }}</p>
      <p v-if="f.fix_hint"><b>수정 방향</b> {{ f.fix_hint }}</p>
      <p><span class="badge" v-for="r in f.standard_refs" :key="r">{{ r }}</span> <span class="badge" v-for="t in f.tools_used" :key="t">🔧 {{ t }}</span></p>
      <div v-if="auth.user?.role !== 'AUDITOR' && f.status === 'OPEN'" class="mt"><input v-model="reason" placeholder="오탐 사유" />
        <button class="btn mt" :disabled="!reason" @click="falsePositive">오탐 처리</button><p v-if="error" class="error">{{ error }}</p></div></div>
    <div class="card"><h3>근거</h3>
      <div v-for="(e, i) in f.evidence" :key="i" class="mt"><b>{{ e.kind }}</b> <span class="muted">{{ e.file_path ?? e.probe_id }}{{ e.line_start ? ':' + e.line_start : '' }}</span><pre>{{ e.snippet_masked }}</pre></div>
      <h3 v-if="f.risk_paths?.length">결합 위험</h3><p v-for="r in f.risk_paths" :key="r.id"><Sev :v="r.severity" /> {{ r.title }}</p></div>
  </div>
</template>
