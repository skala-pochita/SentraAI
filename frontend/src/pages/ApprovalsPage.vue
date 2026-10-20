<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { get, post, ApiError } from '../api/client'
import type { Approval } from '../api/types'
import Sev from '../components/Sev.vue'
import Icon from '../components/Icon.vue'
const rows = ref<Approval[]>([])
const error = ref('')
const loaded = ref(false)
const load = async () => { rows.value = await get<Approval[]>('/approvals'); loaded.value = true }
onMounted(load)
async function decide(a: Approval, decision: 'APPROVE' | 'REJECT') {
  try { await post(`/approvals/${a.id}/decision`, { decision, comment: '' }); await load() }
  catch (e) { error.value = e instanceof ApiError ? e.body.message : String(e) }
}
</script>
<template>
  <p v-if="error" class="error"><Icon name="alert" :size="16" />{{ error }}</p>
  <div v-if="loaded && !rows.length" class="card empty"><Icon name="check-circle" /><b>대기 중인 승인이 없습니다</b><span>CRITICAL 수정안이 생기면 이곳에 표시됩니다.</span></div>
  <div class="stack" style="gap:14px">
    <div v-for="a in rows" :key="a.id" class="card hold">
      <div class="card-head">
        <span class="badge tone warn"><Icon name="lock" />승인 필요</span>
        <h3 style="flex:1; min-width:200px">{{ a.summary }}</h3>
        <RouterLink :to="`/audits/${a.audit_id}`" class="btn ghost sm">감사 <span class="mono">{{ a.audit_id.slice(0, 8) }}</span><Icon name="chevron" :size="14" /></RouterLink>
      </div>
      <div class="stack" style="gap:6px">
        <RouterLink v-for="f in a.findings" :key="f.id" :to="`/findings/${f.id}`" class="frow"><Sev :v="f.severity" /><span style="flex:1; min-width:0">{{ f.title }}</span><span class="badge mono">{{ f.category }}</span></RouterLink>
      </div>
      <div class="row mt">
        <button class="btn primary" @click="decide(a, 'APPROVE')"><Icon name="check" :size="15" />승인하고 PR 생성</button>
        <button class="btn danger" @click="decide(a, 'REJECT')"><Icon name="x" :size="15" />반려</button>
      </div>
    </div>
  </div>
</template>
<style scoped>
.frow { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-radius: 10px; background: var(--surface-2); border: 1px solid var(--border); color: var(--text); transition: border-color .15s; }
.frow:hover { border-color: var(--border-strong); color: var(--text); }
</style>
