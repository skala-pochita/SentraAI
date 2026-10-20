<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { tokens } from '../api/client'
import { renderMarkdown } from '../utils/markdown'
import Icon from '../components/Icon.vue'
import AuditTabs from '../components/AuditTabs.vue'
const id = useRoute().params.id as string
const md = ref('')
const raw = ref(false)
onMounted(async () => {
  const r = await fetch(`/api/v1/audits/${id}/report.md`, { headers: { Authorization: `Bearer ${tokens.access}` } })
  md.value = await r.text()
})
const html = computed(() => renderMarkdown(md.value))
function download() {
  const url = URL.createObjectURL(new Blob([md.value], { type: 'text/markdown' }))
  const a = document.createElement('a')
  a.href = url; a.download = `audit_${id.slice(0, 8)}.md`; a.click()
  URL.revokeObjectURL(url)
}
</script>
<template>
  <AuditTabs :id="id" />
  <div class="card" style="padding:28px 32px">
    <div class="row mb" style="justify-content:flex-end">
      <div class="tabs" style="margin:0"><button :class="{ on: !raw }" @click="raw = false">미리보기</button><button :class="{ on: raw }" @click="raw = true">원문</button></div>
      <button class="btn sm" :disabled="!md" @click="download"><Icon name="download" :size="14" />.md 다운로드</button>
    </div>
    <p v-if="!md" class="muted">보고서를 불러오는 중…</p>
    <pre v-else-if="raw" style="white-space:pre-wrap">{{ md }}</pre>
    <!-- renderMarkdown 은 입력 전체를 먼저 이스케이프한다 -->
    <article v-else class="md" v-html="html" />
  </div>
</template>
