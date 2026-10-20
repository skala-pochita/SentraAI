<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { get } from '../api/client'
import Icon from '../components/Icon.vue'
import AuditTabs from '../components/AuditTabs.vue'
interface Run { id: string; attempt: number; success: boolean; reply_masked: string; tool_calls: unknown[]; judged_by: string }
interface Row { probe_id: string; category: string; channel: string; runs: Run[] }
const id = useRoute().params.id as string
const data = ref<{ probes: Row[]; asr_by_category: Record<string, number>; coverage: { target_id: string; static: string; attacks: string; reason: string; model_verification?: string; runtime: { status: string; mode?: string; reason?: string } }[] } | null>(null)
const open = ref<Run | null>(null)
const openProbe = ref('')
onMounted(async () => { data.value = await get(`/audits/${id}/probes`) })
const tone = (v: number) => (v >= 0.5 ? 'var(--critical)' : v > 0 ? 'var(--warn)' : 'var(--ok)')
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
            <td><button v-for="r in p.runs" :key="r.id" class="attempt" :class="[r.success ? 'bad' : 'ok', { sel: open?.id === r.id }]" @click="pick(p, r)">
              <Icon :name="r.success ? 'alert' : 'shield'" :size="13" />{{ r.success ? '성공' : '차단' }}</button></td>
          </tr></tbody>
        </table></div>
      </div>
      <div v-if="open" class="card">
        <div class="card-head"><h3><Icon name="bot" :size="16" />{{ openProbe }} · 시도 {{ open.attempt }}</h3>
          <span class="badge tone" :class="open.success ? 'bad' : 'ok'">{{ open.success ? '공격 성공' : '차단됨' }}</span>
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
</style>
