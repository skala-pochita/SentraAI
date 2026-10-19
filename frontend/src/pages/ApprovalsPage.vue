<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { get, post, ApiError } from '../api/client'
import type { Approval } from '../api/types'
import Sev from '../components/Sev.vue'
const rows = ref<Approval[]>([])
const error = ref('')
const load = async () => { rows.value = await get<Approval[]>('/approvals') }
onMounted(load)
async function decide(a: Approval, decision: 'APPROVE' | 'REJECT') {
  try { await post(`/approvals/${a.id}/decision`, { decision, comment: '' }); await load() }
  catch (e) { error.value = e instanceof ApiError ? e.body.message : String(e) }
}
</script>
<template>
  <p v-if="error" class="error">{{ error }}</p>
  <p v-if="!rows.length" class="muted">대기 중인 승인이 없습니다.</p>
  <div v-for="a in rows" :key="a.id" class="card mt hold">
    <div class="row"><b>{{ a.summary }}</b><RouterLink :to="`/audits/${a.audit_id}`">감사 보기</RouterLink></div>
    <p v-for="f in a.findings" :key="f.id"><Sev :v="f.severity" /> <RouterLink :to="`/findings/${f.id}`">{{ f.title }}</RouterLink></p>
    <div class="row"><button class="btn primary" @click="decide(a, 'APPROVE')">승인</button><button class="btn danger" @click="decide(a, 'REJECT')">반려</button></div>
  </div>
</template>
