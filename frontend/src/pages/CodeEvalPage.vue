<script setup lang="ts">
// 명세 16장 '모델 비교': A/B 지표 표, 카테고리별 재현율, F1 vs 비용(토큰) 산점도, 감사기 인젝션 견고성, 케이스별 차이
import { computed, onMounted, ref } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { BarChart, ScatterChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { get, post } from '../api/client'
import { useAuth } from '../stores/auth'
import { chartBase, CONFIG_COLORS, useTheme } from '../composables/theme'
import Icon from '../components/Icon.vue'
use([BarChart, ScatterChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

interface Run { id: string; configs: string[]; status: string; repeat: number; created_at: string; dataset_version: string }
interface Stat { mean: number | null; std: number | null }
interface Detail extends Run {
  results: { config: string; repeat_idx: number; metrics: Record<string, unknown>; per_case: Record<string, string> }[]
  summary: Record<string, Record<string, Stat>>
}
const auth = useAuth()
const { isDark } = useTheme()
const runs = ref<Run[]>([])
const detail = ref<Detail | null>(null)
const busy = ref(false)
const msg = ref('')
const CONFIGS = ['A_nano_single', 'B_nano_harness']
const METRICS = ['code_recall', 'code_precision', 'code_f1', 'static_evasion_recall', 'agent_recall', 'probe_accuracy',
  'severity_accuracy', 'evidence_accuracy', 'auditor_robustness', 'fp_count_on_decoys', 'tokens', 'cost_usd', 'latency_s']
const LOWER_BETTER = new Set(['fp_count_on_decoys', 'tokens', 'cost_usd', 'latency_s'])
const LABELS: Record<string, string> = {
  code_recall: '코드 재현율', code_precision: '코드 정밀도', code_f1: '코드 F1', static_evasion_recall: '정적 회피 재현율',
  agent_recall: 'Agent 재현율', probe_accuracy: 'probe 판정 정확도', severity_accuracy: '심각도 정확도', evidence_accuracy: '근거 정확도',
  auditor_robustness: '감사기 인젝션 견고성', fp_count_on_decoys: '함정 오탐 수', tokens: '토큰', cost_usd: '비용(USD)', latency_s: '지연(초)',
}
const CFG_INFO: Record<string, { tag: string; title: string; desc: string }> = {
  A_nano_single: { tag: 'A', title: '작은 모델 · 단일 프롬프트', desc: '기준선' },
  B_nano_harness: { tag: 'B', title: '작은 모델 + AI Agent', desc: 'LangGraph + MCP + 가드레일' },
}

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

const cfgs = computed(() => CONFIGS.filter((c) => c in (detail.value?.summary ?? {})))
const round = (n: number) => (Math.abs(n) >= 100 ? Math.round(n).toLocaleString() : String(Math.round(n * 100) / 100))
const fmtMean = (s?: Stat) => (s?.mean == null ? '-' : round(s.mean))
const fmtStd = (s?: Stat) => (s?.std ? `± ${round(s.std)}` : '')
const best = (m: string) => {
  const vals = cfgs.value.map((c) => detail.value?.summary[c][m]?.mean).filter((v): v is number => v != null)
  if (!vals.length || vals.every((v) => v === vals[0])) return null
  return LOWER_BETTER.has(m) ? Math.min(...vals) : Math.max(...vals)
}
const isBest = (m: string, c: string) => { const b = best(m); return b != null && detail.value?.summary[c][m]?.mean === b }
const color = (c: string) => CONFIG_COLORS[c] ?? '#94a3b8'
const short = (c: string) => CFG_INFO[c]?.tag ?? c

const catOpt = computed(() => {
  void isDark.value
  const t = chartBase()
  const first = (c: string) => detail.value?.results.find((r) => r.config === c)?.metrics.recall_by_category as Record<string, number> | undefined
  const cats = Object.keys(first(cfgs.value[0]) ?? {})
  return {
    ...t.base, tooltip: { ...t.base.tooltip, trigger: 'axis', axisPointer: { type: 'shadow' } }, legend: { ...t.base.legend, top: 0 },
    grid: { left: 8, right: 16, top: 36, bottom: 8, containLabel: true },
    xAxis: { type: 'value', max: 1, ...t.axis }, yAxis: { type: 'category', data: cats, ...t.axis, splitLine: { show: false } },
    series: cfgs.value.map((c) => ({ type: 'bar', name: short(c), data: cats.map((k) => first(c)?.[k] ?? 0), barGap: '20%', barMaxWidth: 10,
      itemStyle: { color: color(c), borderRadius: [0, 4, 4, 0] } })),
  }
})
const scatterOpt = computed(() => {
  void isDark.value
  const t = chartBase()
  return {
    ...t.base,
    tooltip: { ...t.base.tooltip, formatter: (p: { seriesName: string; value: number[] }) => `<b>${p.seriesName}</b><br/>tokens ${Math.round(p.value[0]).toLocaleString()} · F1 ${p.value[1]}` },
    legend: { ...t.base.legend, top: 0 }, grid: { left: 8, right: 24, top: 40, bottom: 8, containLabel: true },
    xAxis: { type: 'value', name: '토큰(비용)', nameLocation: 'middle', nameGap: 26, ...t.axis },
    yAxis: { type: 'value', name: 'code F1', max: 1, ...t.axis },
    series: cfgs.value.map((c) => ({ type: 'scatter', name: short(c), symbolSize: 26,
      itemStyle: { color: color(c), shadowBlur: 18, shadowColor: color(c), opacity: .9 },
      label: { show: true, formatter: short(c), color: '#03140e', fontWeight: 700 },
      data: [[detail.value?.summary[c].tokens?.mean ?? 0, detail.value?.summary[c].code_f1?.mean ?? 0]] })),
  }
})
const cases = computed(() => {
  const first: Record<string, Record<string, string>> = {}
  for (const c of cfgs.value) first[c] = detail.value?.results.find((r) => r.config === c)?.per_case ?? {}
  const keys = [...new Set(Object.values(first).flatMap((pc) => Object.keys(pc)))].sort()
  return keys.map((k) => ({
    k, row: cfgs.value.map((c) => first[c][k] ?? '-'),
    bOnly: ['TP', 'ROBUST'].includes(first.B_nano_harness?.[k] ?? '') && cfgs.value.filter((c) => c !== 'B_nano_harness').every((c) => !['TP', 'ROBUST'].includes(first[c][k] ?? '')),
  }))
})
const verdictTone = (v: string) => (['TP', 'OK', 'ROBUST'].includes(v) ? 'ok' : ['FN', 'FP'].includes(v) ? 'bad' : ['PARTIAL', 'DETECTED_ONLY'].includes(v) ? 'warn' : '')
const f1 = (c: string) => detail.value?.summary[c]?.code_f1
</script>

<template>
  <div class="stack" style="gap:16px">
    <div class="card">
      <div class="row" style="justify-content:space-between; gap:16px">
        <div style="min-width:0">
          <h3 style="font-size:16px">A/B 비교 실험 <span class="muted" style="font-weight:400">· 코드·Agent 감사</span></h3>
          <p class="muted small" style="margin:0">같은 정답셋으로 두 구성을 반복 실행합니다. 결과는 그대로 기록합니다.</p>
        </div>
        <div class="row">
          <select v-if="runs.length" :value="detail?.id" aria-label="실험 선택" style="width:auto; min-width:240px" @change="load(($event.target as HTMLSelectElement).value)">
            <option v-for="r in runs" :key="r.id" :value="r.id">{{ r.id.slice(0, 8) }} · {{ r.status }} · {{ r.created_at.slice(0, 16).replace('T', ' ') }}</option>
          </select>
          <button class="btn" @click="load(detail?.id)"><Icon name="refresh" :size="15" />새로고침</button>
          <button v-if="auth.isAdmin" class="btn primary" :disabled="busy" @click="start"><Icon name="play" :size="15" />실험 실행 (A·B × 1)</button>
        </div>
      </div>
      <p v-if="msg" class="callout info mt small"><Icon name="clock" :size="16" />{{ msg }}</p>
      <div v-if="!runs.length" class="empty"><Icon name="flask" /><b>아직 실험이 없습니다</b></div>
    </div>

    <div v-if="detail && cfgs.length" class="grid cols-2">
      <div v-for="c in cfgs" :key="c" class="card cfg" :class="{ hero2: c === 'B_nano_harness' }" :style="{ '--c': color(c) }">
        <div class="row"><span class="tag">{{ short(c) }}</span><span style="min-width:0"><b>{{ CFG_INFO[c]?.title ?? c }}</b><span class="muted small" style="display:block">{{ CFG_INFO[c]?.desc }}</span></span></div>
        <div class="row mt" style="align-items:baseline; gap:8px"><span class="big num">{{ fmtMean(f1(c)) }}</span><span class="muted small">code F1 {{ fmtStd(f1(c)) }}</span></div>
        <div class="progress mt-s"><span :style="{ width: `${(f1(c)?.mean ?? 0) * 100}%`, background: color(c) }" /></div>
        <div class="row small muted mt-s" style="justify-content:space-between"><span><Icon name="cpu" :size="13" /> {{ fmtMean(detail.summary[c].tokens) }} tokens</span><span><Icon name="clock" :size="13" /> {{ fmtMean(detail.summary[c].latency_s) }}s</span></div>
      </div>
    </div>

    <div v-if="detail && cfgs.length" class="card flush">
      <div class="card-head"><h3><Icon name="layers" :size="16" />지표</h3><span class="sub">평균 ± 표준편차 · 굵게 = 가장 좋은 값</span></div>
      <div class="table-wrap mt-s"><table>
        <thead><tr><th>지표</th><th v-for="c in cfgs" :key="c" class="r"><span class="dot" :style="{ background: color(c) }" />{{ c }}</th></tr></thead>
        <tbody><tr v-for="m in METRICS" :key="m">
          <td><span style="color:var(--text)">{{ LABELS[m] ?? m }}</span> <span class="muted mono small">{{ m }}</span></td>
          <td v-for="c in cfgs" :key="c" class="r" :class="{ win: isBest(m, c) }">
            <span class="num">{{ fmtMean(detail.summary[c][m]) }}</span> <span class="muted small">{{ fmtStd(detail.summary[c][m]) }}</span>
          </td>
        </tr></tbody>
      </table></div>
    </div>
    <div v-else-if="detail" class="card empty"><Icon name="clock" /><b>실험 {{ detail.status }}</b><span>결과 대기 중</span></div>

    <div v-if="detail && cfgs.length" class="grid cols-2">
      <div class="card"><div class="card-head"><h3><Icon name="bug" :size="16" />카테고리별 재현율</h3></div><VChart :option="catOpt" style="height:420px" autoresize /></div>
      <div class="card"><div class="card-head"><h3><Icon name="coins" :size="16" />F1 vs 비용(토큰)</h3></div><VChart :option="scatterOpt" style="height:420px" autoresize /></div>
    </div>

    <div v-if="detail && cfgs.length" class="card flush">
      <div class="card-head"><h3><Icon name="file" :size="16" />케이스별 차이</h3><span class="sub"><Icon name="star" :size="13" style="color:var(--warn)" /> = B 만 잡은 케이스 (첫 반복)</span></div>
      <div class="table-wrap mt-s"><table>
        <thead><tr><th>케이스</th><th v-for="c in cfgs" :key="c"><span class="dot" :style="{ background: color(c) }" />{{ c }}</th></tr></thead>
        <tbody><tr v-for="r in cases" :key="r.k" :class="{ bonly: r.bOnly }">
          <td class="mono strong" style="color:var(--text)">{{ r.k }} <Icon v-if="r.bOnly" name="star" :size="13" style="color:var(--warn)" /></td>
          <td v-for="(v, i) in r.row" :key="i"><span class="badge tone mono" :class="verdictTone(v)">{{ v }}</span></td>
        </tr></tbody>
      </table></div>
    </div>
  </div>
</template>

<style scoped>
.cfg { --c: var(--accent); overflow: hidden; }
.cfg::before { content: ''; position: absolute; inset: 0 0 auto; height: 3px; background: var(--c); }
.cfg.hero2 { border-color: color-mix(in srgb, var(--c) 45%, var(--border)); box-shadow: var(--shadow), 0 0 0 1px color-mix(in srgb, var(--c) 18%, transparent), 0 18px 40px -22px var(--c); }
.cfg b { color: var(--text); }
.tag { width: 36px; height: 36px; flex: none; border-radius: 10px; display: grid; place-items: center; font: 700 16px var(--font-display); color: #04110c; background: var(--c); }
.big { font-size: 36px; font-weight: 700; letter-spacing: -.03em; color: var(--text); line-height: 1; }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin-right: 6px; vertical-align: 1px; }
td.win .num { font-weight: 700; color: var(--accent); }
tr.bonly td { background: color-mix(in srgb, var(--warn) 7%, transparent); }
</style>
