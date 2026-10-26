<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { get } from '../api/client'
import { useAuth } from '../stores/auth'
import { renderMarkdown } from '../utils/markdown'
import Icon from '../components/Icon.vue'
import AuditTabs from '../components/AuditTabs.vue'
const route = useRoute()
const id = route.params.id as string
const auth = useAuth()
const view = ref<'report' | 'compliance'>('report')
const canReadCompliance = computed(() => ['ADMIN', 'AUDITOR'].includes(auth.user?.role ?? ''))
const loading = ref(false)
let requestNumber = 0
const md = ref('')
const raw = ref(false)
const error = ref('')
async function load(next: 'report' | 'compliance') {
  const request = ++requestNumber
  view.value = next; md.value = ''; error.value = ''; loading.value = true
  try {
    const result = await get<string>(`/audits/${id}/${next}.md`)
    if (request === requestNumber) md.value = result
  } catch (e) { if (request === requestNumber) error.value = String(e) }
  finally { if (request === requestNumber) loading.value = false }
}
onMounted(() => load(route.query.view === 'compliance' && canReadCompliance.value ? 'compliance' : 'report'))
async function downloadJson() {
  try {
    const data = await get<Record<string, unknown>>(`/audits/${id}/compliance`)
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }))
    const a = document.createElement('a'); a.href = url; a.download = `compliance_${id.slice(0, 8)}.json`; a.click(); URL.revokeObjectURL(url)
  } catch (e) { error.value = String(e) }
}
const html = computed(() => renderMarkdown(md.value))
function download() {
  const url = URL.createObjectURL(new Blob([md.value], { type: 'text/markdown' }))
  const a = document.createElement('a')
  a.href = url; a.download = `${view.value}_${id.slice(0, 8)}.md`; a.click()
  URL.revokeObjectURL(url)
}
</script>
<template>
  <div class="report-navigation">
    <AuditTabs :id="id" />
    <div class="tabs report-kind" aria-label="보고서 종류">
      <button :class="{ on: view === 'report' }" :aria-pressed="view === 'report'" @click="load('report')">감사 보고서</button>
      <button v-if="canReadCompliance" :class="{ on: view === 'compliance' }" :aria-pressed="view === 'compliance'" @click="load('compliance')">규제 검토 권고</button>
    </div>
  </div>
  <div class="card" style="padding:28px 32px">
    <div class="row mb" style="justify-content:flex-end">
      <div class="tabs" style="margin:0"><button :class="{ on: !raw }" @click="raw = false">보고서 읽기</button><button :class="{ on: raw }" @click="raw = true">Markdown 원문 보기</button></div>
      <button v-if="view === 'compliance'" class="btn sm" :disabled="loading || !!error || !md" @click="downloadJson">JSON 다운로드</button>
      <button class="btn sm" :disabled="!md" @click="download"><Icon name="download" :size="14" />보고서 다운로드 (.md)</button>
    </div>
    <div v-if="error" role="alert">
      <p class="error">{{ error }}</p>
      <button class="btn sm" @click="load(view)">다시 불러오기</button>
    </div>
    <p v-else-if="loading" class="muted">보고서를 불러오는 중…</p>
    <pre v-else-if="raw" style="white-space:pre-wrap">{{ md }}</pre>
    <!-- renderMarkdown 은 입력 전체를 먼저 이스케이프한다 -->
    <article v-else class="md" v-html="html" />
  </div>
</template>

<style scoped>
.report-navigation { display: flex; align-items: center; flex-wrap: wrap; gap: 12px 20px; margin-bottom: 20px; }
.report-navigation :deep(.tabs) { margin-bottom: 0; }
.report-kind { flex-shrink: 0; }
</style>
