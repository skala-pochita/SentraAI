<script setup lang="ts">
import { defineAsyncComponent, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { get } from '../api/client'
import Icon from '../components/Icon.vue'
import AuditTabs from '../components/AuditTabs.vue'
import type { Shot } from '../three/stage'
const ProbeArena = defineAsyncComponent(() => import('../components/ProbeArena.vue'))
interface Run { id: string; attempt: number; success: boolean; verdict?: string; reply_masked: string; tool_calls: unknown[]; judged_by: string }
interface Row { probe_id: string; category: string; channel: string; runs: Run[] }
const id = useRoute().params.id as string
const data = ref<{ probes: Row[]; asr_by_category: Record<string, number>; coverage: { target_id: string; static: string; attacks: string; reason: string; model_verification?: string; runtime: { status: string; mode?: string; reason?: string } }[] } | null>(null)
const open = ref<Run | null>(null)
const openProbe = ref('')
const shots = ref<Shot[]>([])
onMounted(async () => { data.value = await get(`/audits/${id}/probes`); replay() })
// 시도 순번별로 모든 probe 가 한 번씩 쏘도록 섞어 일제 사격처럼 재생한다 (적응형 probe 는 기본 probe 발사대에서)
function replay() {
  const all = (data.value?.probes ?? []).flatMap((p) => p.runs.map((r) => ({ lane: p.probe_id.replace(/-A\d+$/, ''), success: r.success, attempt: r.attempt })))
  shots.value = all.sort((a, b) => a.attempt - b.attempt).map(({ lane, success }) => ({ lane, success }))
}
const tone = (v: number) => (v >= 0.5 ? 'var(--critical)' : v > 0 ? 'var(--warn)' : 'var(--ok)')
const uncertain = (r: Run) => r.verdict === 'UNDETERMINED' || r.verdict === 'UNKNOWN'
const stateClass = (r: Run) => uncertain(r) ? 'warn' : r.success ? 'bad' : 'ok'
function pick(p: Row, r: Run) { open.value = r; openProbe.value = p.probe_id }
</script>
<template>
  <AuditTabs :id="id" />
  <div v-for="c in data?.coverage ?? []" :key="c.target_id" class="callout info mb">
    <div><b>권한 검사 {{ c.static }} · 격리 실행 {{ c.runtime.status }} · 공격 검사 {{ c.attacks }}</b>
      <p class="muted small">{{ c.reason || c.runtime.reason }} · 실행 모드: {{ c.runtime.mode ?? '미실행' }} · 모델 검증: {{ c.model_verification ?? '미확인' }}</p>
      <p v-if="c.runtime.mode === 'mock'" class="muted small">모의 에이전트의 도구 실행 검증입니다. 실제 LLM의 방어 성능을 의미하지 않습니다.</p>
    </div>
  </div>
  <div v-if="data && shots.length" class="card mb">
    <div class="card-head"><h3><Icon name="crosshair" :size="16" />공격 재생</h3><span class="muted small">probe 시도 {{ shots.length }}건을 기록 순서대로 다시 보여줍니다</span>
      <span class="spacer" /><button class="btn" @click="replay"><Icon name="refresh" :size="14" />다시 재생</button></div>
    <ProbeArena :feed="shots" />
  </div>
  <div v-if="data" class="grid side">
    <div class="card">
      <div class="card-head"><h3><Icon name="crosshair" :size="16" />카테고리별 공격 성공률</h3></div>
      <p class="muted small" style="margin-top:-6px">ASR(Attack Success Rate) — 낮을수록 안전</p>
      <div v-for="(v, k) in data.asr_by_category" :key="k" class="asr">
        <div class="row" style="justify-content:space-between"><span class="mono small">{{ k }}</span><b class="num" :style="{ color: tone(v) }">{{ Math.round(v * 100) }}%</b></div>
        <div class="progress"><span :style="{ width: `${Math.max(2, v * 100)}%`, background: tone(v) }" /></div>
      </div>
      <div v-if="!Object.keys(data.asr_by_category).length" class="empty"><Icon name="inbox" /><b>probe 결과가 없습니다</b></div>
    </div>
    <div class="stack" style="gap:16px">
      <div class="card flush">
        <div class="table-wrap"><table>
          <thead><tr><th>Probe</th><th>카테고리</th><th>채널</th><th>시도 (클릭 → 대화)</th></tr></thead>
          <tbody><tr v-for="p in data.probes" :key="p.probe_id">
            <td class="mono strong" style="color:var(--text)">{{ p.probe_id }}</td><td><span class="badge mono">{{ p.category }}</span></td><td class="muted">{{ p.channel }}</td>
            <td><button v-for="r in p.runs" :key="r.id" class="attempt" :class="[stateClass(r), { sel: open?.id === r.id }]" @click="pick(p, r)">
              <Icon :name="uncertain(r) || r.success ? 'alert' : 'shield'" :size="13" />{{ r.verdict === 'UNDETERMINED' || r.verdict === 'UNKNOWN' ? '미확인' : r.success ? '성공' : '차단' }}</button></td>
          </tr></tbody>
        </table></div>
      </div>
      <div v-if="open" class="card">
        <div class="card-head"><h3><Icon name="bot" :size="16" />{{ openProbe }} · 시도 {{ open.attempt }}</h3>
          <span class="badge tone" :class="stateClass(open)">{{ open.verdict === 'UNDETERMINED' || open.verdict === 'UNKNOWN' ? '미확인' : open.success ? '공격 성공' : '차단됨' }}</span>
          <span class="spacer" /><span class="muted small">{{ open.judged_by }} 판정</span>
          <button class="icon-btn" aria-label="닫기" @click="open = null"><Icon name="x" :size="16" /></button></div>
        <p class="section-title">응답</p><pre style="white-space:pre-wrap">{{ open.reply_masked }}</pre>
        <p class="section-title mt">도구 호출</p><pre>{{ JSON.stringify(open.tool_calls, null, 1) }}</pre>
      </div>
    </div>
  </div>
</template>
<style scoped>
.asr { display: flex; flex-direction: column; gap: 6px; padding: 10px 0; }
.spacer { flex: 1; }
.attempt.warn { color: var(--warn); border-color: var(--warn); }
</style>
