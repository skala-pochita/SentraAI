<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { get } from '../api/client'
import type { RiskPath } from '../api/types'
import Sev from '../components/Sev.vue'
const id = useRoute().params.id as string
const rows = ref<RiskPath[]>([])
onMounted(async () => { rows.value = await get<RiskPath[]>(`/audits/${id}/risk-paths`) })
</script>
<template>
  <p v-if="!rows.length" class="muted">결합 위험이 없습니다.</p>
  <div v-for="r in rows" :key="r.id" class="card mt"><div class="row"><Sev :v="r.severity" /><b>{{ r.title }}</b><span class="badge">{{ r.rule_id }}</span></div>
    <p>{{ r.explanation }}</p><p class="muted">구성 Finding:
      <RouterLink v-for="f in r.finding_ids" :key="f" :to="`/findings/${f}`">{{ f.slice(0, 8) }} </RouterLink></p></div>
</template>
