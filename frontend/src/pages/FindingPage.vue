<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { get, patch, ApiError } from '../api/client'
import type { Finding } from '../api/types'
import { useAuth } from '../stores/auth'
import Sev from '../components/Sev.vue'
import Icon from '../components/Icon.vue'
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
  <div v-if="f">
    <RouterLink :to="`/audits/${f.audit_id}/findings`" class="btn ghost sm mb"><Icon name="back" :size="14" />보안 문제 목록으로 돌아가기</RouterLink>
    <div class="grid split">
      <div class="stack" style="gap:16px">
        <div class="card" :style="{ borderTop: `3px solid var(--${f.severity.toLowerCase()})` }">
          <div class="row" style="margin-bottom:10px"><Sev :v="f.severity" /><span class="badge mono">{{ f.category }}</span><span class="status" :class="f.status === 'OPEN' ? 'WAITING_APPROVAL' : f.status === 'RESOLVED' ? 'COMPLETED' : ''">{{ f.status }}</span></div>
          <h2 style="font-size:19px; font-family:var(--font-display)">{{ f.title }}</h2>
          <p style="color:var(--text-2)">{{ f.reasoning_summary }}</p>
          <div class="grid cols-3 mt">
            <div class="kv"><span class="muted small">규칙</span><span class="mono">{{ f.rule_id ?? 'LLM' }}</span></div>
            <div class="kv"><span class="muted small">출처</span><span class="mono">{{ f.source }}</span></div>
            <div class="kv"><span class="muted small">신뢰도</span>
              <div class="meter"><div class="progress"><span :style="{ width: `${Math.round(f.confidence * 100)}%` }" /></div><span class="v">{{ f.confidence.toFixed(2) }}</span></div></div>
          </div>
        </div>
        <div v-if="f.fix_hint" class="callout ok"><Icon name="wrench" :size="18" /><div><b>수정 방향</b><p style="margin:4px 0 0; color:var(--text-2)">{{ f.fix_hint }}</p></div></div>
        <div v-if="f.standard_refs?.length || f.tools_used?.length" class="card">
          <template v-if="f.standard_refs?.length"><p class="section-title">표준 참조</p><div class="chips"><span v-for="r in f.standard_refs" :key="r" class="badge tone info">{{ r }}</span></div></template>
          <template v-if="f.tools_used?.length"><p class="section-title" :class="{ mt: f.standard_refs?.length }">사용한 도구</p>
            <div class="chips"><span v-for="t in f.tools_used" :key="t" class="badge mono"><Icon name="wrench" />{{ t }}</span></div></template>
        </div>
        <div v-if="auth.user?.role !== 'AUDITOR' && f.status === 'OPEN'" class="card">
          <p class="section-title">보안 문제가 아닌 것으로 표시</p>
          <div class="row"><input v-model="reason" placeholder="오탐 사유를 입력하세요" style="flex:1; min-width:200px" />
            <button class="btn" :disabled="!reason" @click="falsePositive"><Icon name="minus-circle" :size="15" />보안 문제가 아닌 것으로 표시</button></div>
          <p v-if="error" class="error mt"><Icon name="alert" :size="16" />{{ error }}</p>
        </div>
      </div>
      <div class="stack" style="gap:16px">
        <div class="card">
          <div class="card-head"><h3><Icon name="code" :size="16" />근거</h3><span class="sub">{{ f.evidence.length }}개</span></div>
          <div v-for="(e, i) in f.evidence" :key="i" style="margin-bottom:14px">
            <div class="code-head"><span class="dots"><i /><i /><i /></span><span class="badge tone violet">{{ e.kind }}</span>
              <span style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap">{{ e.file_path ?? e.probe_id }}{{ e.line_start ? ':' + e.line_start : '' }}</span></div>
            <pre>{{ e.snippet_masked }}</pre>
          </div>
        </div>
        <div v-if="f.risk_paths?.length" class="card">
          <div class="card-head"><h3><Icon name="share" :size="16" />연결된 공격 경로</h3></div>
          <div v-for="r in f.risk_paths" :key="r.id" class="row" style="padding:8px 0; border-bottom:1px solid var(--border)"><Sev :v="r.severity" />{{ r.title }}</div>
        </div>
      </div>
    </div>
  </div>
</template>
<style scoped>
.kv { display: flex; flex-direction: column; gap: 4px; padding: 10px 12px; border-radius: 10px; background: var(--surface-2); border: 1px solid var(--border); min-width: 0; }
.kv .mono { color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
