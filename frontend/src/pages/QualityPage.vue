<script setup lang="ts">
// ISO/IEC 25010 9개 특성 카드 (목표 대비 측정값) — GET /quality
import { computed, onMounted, ref } from 'vue'
import { get } from '../api/client'
import Icon from '../components/Icon.vue'
interface Card { characteristic: string; design: string; metric: string; target: string; measured: unknown; met: boolean | null; note: string }
const data = ref<{ standard: string; cards: Card[]; eval_run_id: string | null } | null>(null)
onMounted(async () => { data.value = await get('/quality') })

const LABELS: Record<string, string> = {
  recall: '재현율', precision: '정밀도', evidence_accuracy: '근거 정확도', latency_s: 'B 지연(초)', tokens: '토큰',
  user_audits_median_s: '실감사 중앙값(초)', completed: '완료', finished_total: '종료 합계', dlq_new: '미처리 DLQ',
  files_per_rule: '규칙당 파일', rules_loaded: '로드된 규칙',
}
const fmt = (v: unknown) => {
  if (typeof v !== 'number') return String(v)
  if (Number.isInteger(v)) return v.toLocaleString()
  return Math.abs(v) >= 100 ? Math.round(v).toLocaleString() : String(Math.round(v * 1000) / 1000)
}
// 측정값을 key/value 타일 목록으로 정규화 (객체·숫자·문자열 모두 카드 폭 안에서 줄바꿈)
const tiles = (v: unknown): { k: string; v: string }[] | null => {
  if (v == null) return null
  if (typeof v === 'object') return Object.entries(v as Record<string, unknown>).map(([k, x]) => ({ k: LABELS[k] ?? k, v: fmt(x) }))
  return null
}
const scalar = (v: unknown) => (v == null ? '미측정' : typeof v === 'object' ? '' : fmt(v))
const state = (c: Card) => (c.met === true ? 'ok' : c.met === false ? 'bad' : 'na')
const STATE_LABEL = { ok: '충족', bad: '미달', na: '미측정' } as const
const STATE_ICON = { ok: 'check-circle', bad: 'x-circle', na: 'minus-circle' } as const
const metCount = computed(() => data.value?.cards.filter((c) => c.met === true).length ?? 0)
const failCount = computed(() => data.value?.cards.filter((c) => c.met === false).length ?? 0)
const total = computed(() => data.value?.cards.length ?? 0)
const ring = computed(() => {
  const r = 42, c = 2 * Math.PI * r
  return { r, c, dash: total.value ? (metCount.value / total.value) * c : 0 }
})
</script>
<template>
  <div v-if="data">
    <div class="card summary mb">
      <svg width="104" height="104" viewBox="0 0 104 104" aria-hidden="true">
        <circle cx="52" cy="52" :r="ring.r" fill="none" stroke="var(--surface-3)" stroke-width="10" />
        <circle cx="52" cy="52" :r="ring.r" fill="none" stroke="url(#qg)" stroke-width="10" stroke-linecap="round"
          :stroke-dasharray="`${ring.dash} ${ring.c}`" transform="rotate(-90 52 52)" style="transition:stroke-dasharray .8s var(--ease)" />
        <defs><linearGradient id="qg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="var(--accent)" /><stop offset="1" stop-color="var(--accent-2)" /></linearGradient></defs>
        <text x="52" y="50" text-anchor="middle" class="ring-n">{{ metCount }}/{{ total }}</text>
        <text x="52" y="68" text-anchor="middle" class="ring-l">충족</text>
      </svg>
      <div style="min-width:0">
        <h3 style="font-size:17px; font-family:var(--font-display)">{{ data.standard }} 품질 특성</h3>
        <p class="muted" style="margin:0 0 10px">설계 대응과 목표를 실측값으로 확인합니다. 미달 항목은 수치를 그대로 보고합니다.</p>
        <div class="chips">
          <span class="badge tone ok"><Icon name="check-circle" />충족 {{ metCount }}</span>
          <span class="badge tone bad"><Icon name="x-circle" />미달 {{ failCount }}</span>
          <span class="badge"><Icon name="minus-circle" />미측정 {{ total - metCount - failCount }}</span>
          <RouterLink v-if="data.eval_run_id" to="/eval" class="badge tone info"><Icon name="flask" />비교 실험 <span class="mono">{{ data.eval_run_id.slice(0, 8) }}</span></RouterLink>
          <span v-else class="badge">비교 실험 없음</span>
        </div>
      </div>
    </div>

    <div class="grid cols-3 qgrid">
      <article v-for="c in data.cards" :key="c.characteristic" class="card q" :class="state(c)">
        <header class="q-head">
          <h3>{{ c.characteristic }}</h3>
          <span class="badge tone" :class="state(c) === 'na' ? '' : state(c)"><Icon :name="STATE_ICON[state(c)]" />{{ STATE_LABEL[state(c)] }}</span>
        </header>
        <p class="q-design">{{ c.design }}</p>
        <div class="q-metric">
          <span class="muted small">지표</span><b>{{ c.metric }}</b>
          <span class="muted small" style="margin-top:6px">목표</span><span class="target mono">{{ c.target }}</span>
        </div>
        <div class="q-measured">
          <span class="section-title" style="margin:0 0 8px">측정값</span>
          <div v-if="tiles(c.measured)" class="tiles">
            <div v-for="t in tiles(c.measured)" :key="t.k" class="tile"><span class="k">{{ t.k }}</span><span class="v num">{{ t.v }}</span></div>
          </div>
          <div v-else class="scalar" :class="{ text: typeof c.measured === 'string' || c.measured == null }">{{ scalar(c.measured) }}</div>
        </div>
        <p v-if="c.note" class="q-note"><Icon name="alert" :size="13" />{{ c.note }}</p>
      </article>
    </div>
  </div>
</template>
<style scoped>
.summary { display: flex; align-items: center; gap: 24px; }
.ring-n { font: 700 22px var(--font-display); fill: var(--text); }
.ring-l { font: 500 11px var(--font-sans); fill: var(--muted); }
.qgrid { align-items: stretch; }
.q { --c: var(--border-strong); display: flex; flex-direction: column; gap: 14px; overflow: hidden; }
.q::before { content: ''; position: absolute; left: 0; right: 0; top: 0; height: 3px; background: var(--c); opacity: .9; }
.q.ok { --c: var(--ok); } .q.bad { --c: var(--critical); }
.q-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
.q-head h3 { margin: 0; font-size: 16px; font-family: var(--font-display); }
.q-design { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.6; }
.q-metric { display: flex; flex-direction: column; gap: 2px; padding: 12px 14px; border-radius: 12px; background: var(--surface-2); border: 1px solid var(--border); }
.q-metric b { color: var(--text); font-weight: 600; font-size: 13.5px; overflow-wrap: anywhere; }
.target { color: var(--text-2); overflow-wrap: anywhere; }
.q-measured { margin-top: auto; }
.tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 110px), 1fr)); gap: 8px; }
.tile { display: flex; flex-direction: column; gap: 2px; padding: 10px 12px; border-radius: 10px; min-width: 0;
  background: color-mix(in srgb, var(--c) 6%, var(--surface-2)); border: 1px solid color-mix(in srgb, var(--c) 18%, var(--border)); }
.tile .k { font-size: 11.5px; color: var(--muted); overflow-wrap: anywhere; }
.tile .v { font-size: 19px; font-weight: 700; color: var(--text); letter-spacing: -.02em; overflow-wrap: anywhere; }
.scalar { font: 700 30px var(--font-display); color: var(--text); letter-spacing: -.03em; overflow-wrap: anywhere; }
.scalar.text { font: 500 13px/1.55 var(--font-mono); letter-spacing: 0; color: var(--text-2); padding: 10px 12px; border-radius: 10px; background: var(--surface-2); border: 1px solid var(--border); }
.q-note { display: flex; gap: 6px; align-items: flex-start; margin: 0; font-size: 12px; color: var(--muted); }
.q-note .icon { margin-top: 2px; color: var(--warn); }
@media (max-width: 560px) { .summary { flex-direction: column; align-items: flex-start; } }
</style>
