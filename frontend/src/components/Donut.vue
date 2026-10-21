<script setup lang="ts">
// 의존성 없는 SVG 도넛. 개요 화면이 ECharts(약 147KB gzip)를 첫 화면에서 불러오지 않게 한다.
import { computed } from 'vue'

const props = defineProps<{ data: { name: string; value: number; color: string }[]; label?: string }>()

const R = 34.5 // 바깥 40, 안쪽 29 → 기존 radius ['58%', '80%'] 비율
const C = 2 * Math.PI * R
const GAP = 1.2

const total = computed(() => props.data.reduce((a, d) => a + d.value, 0))
const arcs = computed(() => {
  let acc = 0
  return props.data
    .filter((d) => d.value > 0)
    .map((d) => {
      const len = (d.value / total.value) * C
      const gap = props.data.length > 1 ? Math.min(GAP, len / 2) : 0
      const arc = { ...d, dash: `${len - gap} ${C - len + gap}`, offset: -acc }
      acc += len
      return arc
    })
})
const summary = computed(() => props.data.map((d) => `${d.name} ${d.value}`).join(', '))
</script>

<template>
  <svg viewBox="0 0 100 100" class="donut" role="img" :aria-label="`${label ?? '분포'}: ${summary}`">
    <g transform="rotate(-90 50 50)">
      <circle
        v-for="a in arcs" :key="a.name" cx="50" cy="50" :r="R" fill="none" :stroke="a.color" stroke-width="11"
        :stroke-dasharray="a.dash" :stroke-dashoffset="a.offset"
      ><title>{{ a.name }}: {{ a.value }}</title></circle>
    </g>
    <text x="50" y="49" text-anchor="middle" class="donut-total">{{ total }}</text>
    <text x="50" y="61" text-anchor="middle" class="donut-label">{{ label ?? '' }}</text>
  </svg>
</template>

<style scoped>
.donut { display: block; height: 240px; width: 100%; }
.donut circle { transition: stroke-width .15s; }
.donut circle:hover { stroke-width: 13; }
.donut-total { font: 700 15px 'Space Grotesk', sans-serif; fill: var(--text); }
.donut-label { font: 5px 'IBM Plex Sans KR', sans-serif; fill: var(--muted); }
</style>
