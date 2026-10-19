<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { get } from '../api/client'
import type { Finding } from '../api/types'
import Sev from '../components/Sev.vue'
const id = useRoute().params.id as string
const rows = ref<Finding[]>([])
const sev = ref('')
const q = ref('')
onMounted(async () => { rows.value = await get<Finding[]>(`/audits/${id}/findings`) })
const shown = computed(() => rows.value.filter((f) => (!sev.value || f.severity === sev.value) && (!q.value || (f.title + f.category).toLowerCase().includes(q.value.toLowerCase()))))
</script>
<template>
  <div class="card">
    <div class="row"><select v-model="sev" style="width:140px"><option value="">전체 심각도</option><option v-for="s in ['CRITICAL','HIGH','MEDIUM','LOW','INFO']" :key="s">{{ s }}</option></select>
      <input v-model="q" placeholder="검색" style="width:240px" /><span class="muted">{{ shown.length }} / {{ rows.length }}</span></div>
    <table class="mt"><tr><th>심각도</th><th>카테고리</th><th>제목</th><th>출처</th><th>신뢰도</th><th>상태</th></tr>
      <tr v-for="f in shown" :key="f.id"><td><Sev :v="f.severity" /></td><td>{{ f.category }}</td>
        <td><RouterLink :to="`/findings/${f.id}`">{{ f.title }}</RouterLink></td><td>{{ f.source }}</td><td>{{ f.confidence }}</td><td>{{ f.status }}</td></tr></table>
  </div>
</template>
