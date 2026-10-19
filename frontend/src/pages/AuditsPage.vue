<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { get } from '../api/client'
import type { Audit, Paged } from '../api/types'
const rows = ref<Audit[]>([])
onMounted(async () => { rows.value = (await get<Paged<Audit>>('/audits?size=100')).items })
</script>
<template>
  <div class="card"><table>
    <tr><th>ID</th><th>상태</th><th>모드</th><th>Finding</th><th>시작</th></tr>
    <tr v-for="a in rows" :key="a.id">
      <td><RouterLink :to="`/audits/${a.id}`">{{ a.id.slice(0, 8) }}</RouterLink></td>
      <td><span class="status" :class="a.status">{{ a.status }}</span></td><td>{{ a.mode }}</td>
      <td>{{ a.summary?.total ?? '-' }}</td><td class="muted">{{ a.created_at?.slice(0, 16) }}</td>
    </tr></table></div>
</template>
