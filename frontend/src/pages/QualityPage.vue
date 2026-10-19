<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { get } from '../api/client'
import type { Audit, Paged } from '../api/types'
use([BarChart, GridComponent, TooltipComponent, CanvasRenderer])
const audits = ref<Audit[]>([])
onMounted(async () => { audits.value = (await get<Paged<Audit>>('/audits?size=50')).items.filter((a) => a.status === 'COMPLETED') })
const opt = computed(() => ({
  tooltip: {}, xAxis: { type: 'category', data: audits.value.map((a) => a.id.slice(0, 6)) }, yAxis: [{ type: 'value', name: 'Finding' }, { type: 'value', name: 'USD' }],
  series: [{ type: 'bar', name: 'Finding', data: audits.value.map((a) => a.summary?.total ?? 0) }, { type: 'bar', name: 'USD', yAxisIndex: 1, data: audits.value.map((a) => a.summary?.usd ?? 0) }],
}))
</script>
<template><div class="card"><h3>감사별 Finding 수와 비용</h3><VChart v-if="audits.length" :option="opt" style="height:320px" autoresize /><p v-else class="muted">완료된 감사가 없습니다.</p></div></template>
