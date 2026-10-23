<script setup lang="ts">
import { defineAsyncComponent, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { get } from '../api/client'
import type { RiskPath } from '../api/types'
import Sev from '../components/Sev.vue'
import Icon from '../components/Icon.vue'
import AuditTabs from '../components/AuditTabs.vue'
import AttackPathDetails from '../components/AttackPathDetails.vue'
// three.js 는 이 그래프를 그릴 때만 내려받는다
const RiskGraph3D = defineAsyncComponent(() => import('../components/RiskGraph3D.vue'))
const id = useRoute().params.id as string
const rows = ref<RiskPath[]>([])
const loaded = ref(false)
const loadError = ref('')
const gaps = ref<{ target_id: string; rule_id: string; reason: string }[]>([])
const focus = ref<string | null>(null)
const selectedStep = ref<{ pathId: string; stepId: string } | null>(null)
function inspectStep(value: {pathId: string; stepId: string}) {
  selectedStep.value = value
  document.getElementById(`risk-${value.pathId}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
onMounted(async () => {
  try {
    const [paths, report] = await Promise.all([get<RiskPath[]>(`/audits/${id}/risk-paths`), get<{correlation_gaps?: typeof gaps.value}>(`/audits/${id}/report`)])
    rows.value = paths; gaps.value = report.correlation_gaps ?? []; loaded.value = true
  } catch { loadError.value = '결합 위험을 불러오지 못했습니다. 페이지를 새로고침해 주세요.' }

})
</script>
<template>
  <AuditTabs :id="id" />
  <p v-if="loadError" role="alert">{{ loadError }}</p>
  <p v-else-if="!loaded" class="muted">결합 위험을 불러오는 중입니다.</p>
  <div v-if="gaps.length" class="card mb"><b>결합 분석의 미확인 구간</b><p v-for="(g, i) in gaps" :key="i" class="small muted">{{ g.rule_id }} · {{ g.target_id.slice(0, 8) }}: {{ g.reason }}</p></div>
  <div v-if="loaded && !rows.length" class="card empty"><Icon name="check-circle" /><b>결합 위험이 없습니다</b><span>수집된 근거에서 경로를 확인하지 못했습니다. 미확인 구간은 안전 판정을 뜻하지 않습니다.</span></div>
  <div v-if="rows.length" class="card mb">
    <div class="card-head"><h3><Icon name="share" :size="16" />위험 경로 그래프</h3><span class="muted small">아래 경로에 마우스를 올리면 해당 경로만 강조됩니다</span></div>
    <RiskGraph3D :paths="rows" :focus="focus" @inspect="inspectStep" />
  </div>
  <div class="stack" style="gap:14px">
    <div v-for="r in rows" :key="r.id" :id="`risk-${r.id}`" class="card risk" :class="{ on: focus === r.id }" :style="{ '--c': `var(--${r.severity.toLowerCase()})` }" @mouseenter="focus = r.id" @mouseleave="focus = null">
      <div class="row"><Sev :v="r.severity" /><b style="font-size:15px">{{ r.title }}</b><span class="spacer" /><span class="badge mono">{{ r.rule_id }}</span></div>
      <p class="mt-s" style="color:var(--text-2)">{{ r.explanation }}</p>
      <AttackPathDetails :audit-id="id" :path="r" @close="selectedStep = null" :selection="selectedStep?.pathId === r.id ? selectedStep.stepId : null" />
      <div class="row small"><span class="muted">관련 Finding (실행 순서 아님)</span>
        <template v-for="(f, i) in r.finding_ids" :key="f">
          <span v-if="i" class="muted">·</span>
          <RouterLink :to="`/findings/${f}`" class="badge mono">{{ f.slice(0, 8) }}</RouterLink>
        </template>
      </div>
    </div>
  </div>
</template>
<style scoped>
.risk { border-left: 3px solid var(--c); transition: box-shadow .2s var(--ease); }
.risk.on { box-shadow: 0 0 0 1px var(--c), var(--shadow); }
.spacer { flex: 1; }
</style>
