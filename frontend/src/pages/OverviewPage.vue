<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { PieChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { get } from '../api/client'
import type { Audit, Paged, Approval } from '../api/types'
import Stat from '../components/Stat.vue'
import { useAuth } from '../stores/auth'

use([PieChart, TooltipComponent, LegendComponent, CanvasRenderer])
const auth = useAuth()
const audits = ref<Audit[]>([])
const approvals = ref<Approval[]>([])
const error = ref('')

onMounted(async () => {
  try {
    audits.value = (await get<Paged<Audit>>('/audits?size=50')).items
    if (auth.isAdmin) approvals.value = await get<Approval[]>('/approvals')
  } catch (e) { error.value = (e as Error).message }
})
const sev = computed(() => {
  const t: Record<string, number> = {}
  for (const a of audits.value) for (const [k, v] of Object.entries(a.summary?.by_severity ?? {})) t[k] = (t[k] ?? 0) + v
  return t
})
const option = computed(() => ({
  tooltip: {}, legend: { bottom: 0 },
  series: [{ type: 'pie', radius: ['45%', '70%'], data: Object.entries(sev.value).map(([name, value]) => ({ name, value })) }],
}))
const running = computed(() => audits.value.filter((a) => ['QUEUED', 'RUNNING', 'WAITING_APPROVAL', 'WAITING_MERGE'].includes(a.status)).length)
</script>

<template>
  <p v-if="error" class="error">{{ error }}</p>
  <div class="grid cols-4">
    <Stat label="감사" :value="audits.length" />
    <Stat label="진행 중" :value="running" />
    <Stat label="CRITICAL" :value="sev.CRITICAL ?? 0" />
    <Stat label="승인 대기" :value="approvals.length" hint="ADMIN 만 표시" />
  </div>
  <div class="grid cols-2 mt">
    <div class="card"><h3>심각도 분포</h3><VChart v-if="Object.keys(sev).length" :option="option" style="height:260px" autoresize /><p v-else class="muted">완료된 감사가 없습니다.</p></div>
    <div class="card"><h3>최근 감사</h3>
      <table><tr v-for="a in audits.slice(0, 8)" :key="a.id">
        <td><RouterLink :to="`/audits/${a.id}`">{{ a.id.slice(0, 8) }}</RouterLink></td>
        <td><span class="status" :class="a.status">{{ a.status }}</span></td><td>{{ a.mode }}</td><td class="muted">{{ a.created_at?.slice(0, 16) }}</td>
      </tr></table>
    </div>
  </div>
</template>
