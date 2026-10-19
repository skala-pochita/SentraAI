<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { tokens } from '../api/client'
const id = useRoute().params.id as string
const md = ref('')
onMounted(async () => {
  const r = await fetch(`/api/v1/audits/${id}/report.md`, { headers: { Authorization: `Bearer ${tokens.access}` } })
  md.value = await r.text()
})
</script>
<template><div class="card"><pre style="white-space:pre-wrap">{{ md || '보고서를 불러오는 중…' }}</pre></div></template>
