<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { get } from '../api/client'
import Icon from '../components/Icon.vue'
import AuditTabs from '../components/AuditTabs.vue'
interface Run { id: string; attempt: number; success: boolean; reply_masked: string; tool_calls: unknown[]; judged_by: string }
interface Row { probe_id: string; category: string; channel: string; runs: Run[] }
const id = useRoute().params.id as string
const data = ref<{ probes: Row[]; asr_by_category: Record<string, number> } | null>(null)
const open = ref<Run | null>(null)
const openProbe = ref('')
onMounted(async () => { data.value = await get(`/audits/${id}/probes`) })
const tone = (v: number) => (v >= 0.5 ? 'var(--critical)' : v > 0 ? 'var(--warn)' : 'var(--ok)')
function pick(p: Row, r: Run) { open.value = r; openProbe.value = p.probe_id }
</script>
<template>
  <AuditTabs :id="id" />
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
