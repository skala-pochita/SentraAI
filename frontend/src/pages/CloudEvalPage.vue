<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { get } from '../api/client'
interface Metrics { [k: string]: number | string[] | null }
interface Summ { executed: number; skipped: number; skip_reason: string | null; metrics: Metrics | null }
interface Ev { eval_id: string; configs: string[]; bundles: string[]; repetitions: number; models: string[]; concurrency: number; note: string; summary: Record<string, Summ>; superseded_runs: string[] }
const list = ref<{ eval_id: string }[]>([])
const ev = ref<Ev | null>(null)
const ROWS: [string, string][] = [['precision', '정밀도'], ['recall', '재현율'], ['f1', 'F1'], ['path_detection_rate', '경로 탐지율'], ['path_precision', '경로 정밀도'],
  ['evidence_accuracy', '근거 정확도'], ['hold_accuracy', '판단 보류 정확성'], ['reverify_accuracy', '재검증 정확도'], ['injection_failure_rate', '인젝션 실패율'],
  ['completion_rate_normal', '정상 감사 완료율(정상)'], ['completion_rate_attack', '정상 감사 완료율(공격)'], ['usd_per_bundle', '묶음당 비용(USD)'], ['active_s_per_bundle', '묶음당 활성 시간(초)'], ['needs_review', '사람 검토 필요']]
onMounted(async () => {
  list.value = await get('/eval/cloud-runs')
  if (list.value[0]) ev.value = await get(`/eval/cloud-runs/${list.value[0].eval_id}`)
})
function cell(c: string, k: string): string {
  const m = ev.value?.summary[c]?.metrics
  if (!m) return '미측정'
  const v = m[k]
  return v === null || v === undefined ? '해당 없음' : String(v)
}
const diff = computed(() => {
  if (!ev.value || !ev.value.configs.includes('A') || !ev.value.configs.includes('B')) return null
  const a = ev.value.summary.A?.metrics, b = ev.value.summary.B?.metrics
  if (!a || !b) return null
  return ['precision', 'recall', 'f1'].map((k) => [k, typeof a[k] === 'number' && typeof b[k] === 'number' ? ((b[k] as number) - (a[k] as number)) * 100 : null] as const)
})
</script>
<template>
  <div v-if="!ev" class="card"><p class="muted">평가 결과가 없습니다. <code>make eval</code> 로 실행하면 이곳에 표시됩니다.</p></div>
  <div v-else>
    <div class="card"><h3>평가 {{ ev.eval_id }}</h3>
      <p class="muted">묶음 {{ ev.bundles.length }} · 반복 {{ ev.repetitions }} · 병렬도 {{ ev.concurrency }} · 모델 {{ ev.models.join(', ') || '(실행 없음)' }}</p>
      <p class="muted">{{ ev.note }}</p>
      <p v-for="c in ev.configs" :key="c"><b>{{ c }}</b> 실행 {{ ev.summary[c].executed }} / 스킵 {{ ev.summary[c].skipped }} <span v-if="ev.summary[c].skip_reason" class="error">{{ ev.summary[c].skip_reason }}</span></p>
      <p v-if="ev.superseded_runs.length" class="muted">일시 오류로 재실행된 원 기록: {{ ev.superseded_runs.join(', ') }}</p></div>
    <div class="card mt"><table><tr><th>지표</th><th v-for="c in ev.configs" :key="c">{{ c }}</th></tr>
      <tr v-for="[k, label] in ROWS" :key="k"><td>{{ label }}</td><td v-for="c in ev.configs" :key="c">{{ cell(c, k) }}</td></tr></table></div>
    <div v-if="diff" class="card mt"><h4>B − A (퍼센트포인트)</h4><span v-for="[k, v] in diff" :key="k" class="badge">{{ k }} {{ v === null ? '해당 없음' : v.toFixed(1) + 'pp' }}</span>
      <p class="muted">정밀도가 낮아졌다면 재현율 향상과 함께 해석한다. 단일 종합 점수는 만들지 않는다.</p></div>
  </div>
</template>
