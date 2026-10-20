<script setup lang="ts">
// 명세 16장 '모델 비교': A/B/C 지표 표, 카테고리별 재현율, F1 vs 비용(토큰) 산점도, 감사기 인젝션 견고성, 케이스별 차이
import { computed, onMounted, ref } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { BarChart, ScatterChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { get, post } from '../api/client'
import { useAuth } from '../stores/auth'
use([BarChart, ScatterChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

interface Run { id: string; configs: string[]; status: string; repeat: number; created_at: string; dataset_version: string }
interface Stat { mean: number | null; std: number | null }
interface Detail extends Run {
  results: { config: string; repeat_idx: number; metrics: Record<string, unknown>; per_case: Record<string, string> }[]
  summary: Record<string, Record<string, Stat>>
}
const auth = useAuth()
const runs = ref<Run[]>([])
const detail = ref<Detail | null>(null)
const busy = ref(false)
const msg = ref('')
const CONFIGS = ['A_nano_single', 'B_nano_harness', 'C_large_single']
const METRICS = ['code_recall', 'code_precision', 'code_f1', 'static_evasion_recall', 'agent_recall', 'probe_accuracy',
  'severity_accuracy', 'evidence_accuracy', 'auditor_robustness', 'fp_count_on_decoys', 'tokens', 'cost_usd', 'latency_s']

async function load(id?: string) {
  runs.value = await get<Run[]>('/eval-runs')
  const target = id ?? runs.value[0]?.id
  detail.value = target ? await get<Detail>(`/eval-runs/${target}`) : null
}
onMounted(() => load())

async function start() {
  busy.value = true; msg.value = ''
  try {
    const r = await post<Run>('/eval-runs', { configs: CONFIGS, repeat: 1 })
    msg.value = `실험 ${r.id.slice(0, 8)} 시작 — 수 분 걸립니다. 새로고침으로 확인하세요.`
    await load(r.id)
  } catch (e) { msg.value = String(e) } finally { busy.value = false }
}

const cfgs = computed(() => Object.keys(detail.value?.summary ?? {}))
const fmt = (s?: Stat) => (s?.mean == null ? '-' : `${s.mean}${s.std ? ` ± ${s.std}` : ''}`)
const catOpt = computed(() => {
  const first = (c: string) => detail.value?.results.find((r) => r.config === c)?.metrics.recall_by_category as Record<string, number> | undefined
  const cats = Object.keys(first(cfgs.value[0]) ?? {})
  return {
    tooltip: {}, legend: {}, grid: { left: 180 }, xAxis: { type: 'value', max: 1 }, yAxis: { type: 'category', data: cats },
    series: cfgs.value.map((c) => ({ type: 'bar', name: c, data: cats.map((k) => first(c)?.[k] ?? 0) })),
  }
})
const scatterOpt = computed(() => ({
  tooltip: { formatter: (p: { seriesName: string; value: number[] }) => `${p.seriesName}<br/>tokens ${p.value[0]} · F1 ${p.value[1]}` },
  legend: {}, xAxis: { type: 'value', name: '토큰(비용)' }, yAxis: { type: 'value', name: 'code F1', max: 1 },
  series: cfgs.value.map((c) => ({ type: 'scatter', name: c, symbolSize: 18,
    data: [[detail.value?.summary[c].tokens?.mean ?? 0, detail.value?.summary[c].code_f1?.mean ?? 0]] })),
}))
const cases = computed(() => {
  const first: Record<string, Record<string, string>> = {}
  for (const c of cfgs.value) first[c] = detail.value?.results.find((r) => r.config === c)?.per_case ?? {}
  const keys = [...new Set(Object.values(first).flatMap((pc) => Object.keys(pc)))].sort()
  return keys.map((k) => ({
    k, row: cfgs.value.map((c) => first[c][k] ?? '-'),
    bOnly: ['TP', 'ROBUST'].includes(first.B_nano_harness?.[k] ?? '') && cfgs.value.filter((c) => c !== 'B_nano_harness').every((c) => !['TP', 'ROBUST'].includes(first[c][k] ?? '')),
  }))
})
</script>

<template>
  <div class="grid">
    <div class="card">
      <h3>A/B/C 비교 실험 (코드·Agent 감사)</h3>
      <p class="muted">A = 작은 모델 단일 프롬프트 · B = 작은 모델 + Harness + LangGraph + MCP · C = 큰 모델 단일 프롬프트. 결과는 그대로 기록한다.</p>
      <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap">
        <select v-if="runs.length" :value="detail?.id" @change="load(($event.target as HTMLSelectElement).value)" style="width:auto">
          <option v-for="r in runs" :key="r.id" :value="r.id">{{ r.id.slice(0, 8) }} · {{ r.status }} · {{ r.created_at.slice(0, 16) }}</option>
        </select>
        <button v-if="auth.isAdmin" class="btn primary" :disabled="busy" @click="start">실험 실행 (A·B·C × 1)</button>
        <button class="btn" @click="load(detail?.id)">새로고침</button>
        <span class="muted">{{ msg }}</span>
      </div>
      <p v-if="!runs.length" class="muted">아직 실험이 없습니다.</p>
    </div>

    <div v-if="detail && cfgs.length" class="card">
      <h3>지표 (평균 ± 표준편차)</h3>
      <table>
        <thead><tr><th>지표</th><th v-for="c in cfgs" :key="c">{{ c }}</th></tr></thead>
        <tbody><tr v-for="m in METRICS" :key="m"><td>{{ m }}</td><td v-for="c in cfgs" :key="c">{{ fmt(detail.summary[c][m]) }}</td></tr></tbody>
      </table>
    </div>
    <div v-else-if="detail" class="card muted">실험 {{ detail.status }} — 결과 대기 중</div>

    <div v-if="detail && cfgs.length" class="grid cols-2">
      <div class="card"><h3>카테고리별 재현율</h3><VChart :option="catOpt" style="height:420px" autoresize /></div>
      <div class="card"><h3>F1 vs 비용(토큰)</h3><VChart :option="scatterOpt" style="height:420px" autoresize /></div>
    </div>

    <div v-if="detail && cfgs.length" class="card">
      <h3>케이스별 차이 <span class="muted">(⭐ = B 만 잡은 케이스)</span></h3>
      <table>
        <thead><tr><th>케이스</th><th v-for="c in cfgs" :key="c">{{ c }}</th></tr></thead>
        <tbody><tr v-for="r in cases" :key="r.k" :style="r.bOnly ? 'background: color-mix(in srgb, var(--ok) 15%, transparent)' : ''">
          <td>{{ r.k }}{{ r.bOnly ? ' ⭐' : '' }}</td><td v-for="(v, i) in r.row" :key="i">{{ v }}</td></tr></tbody>
      </table>
    </div>
  </div>
</template>
