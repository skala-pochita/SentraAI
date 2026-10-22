<script setup lang="ts">
import { defineAsyncComponent, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { get } from '../api/client'
import type { Finding, RiskPath } from '../api/types'
import Sev from '../components/Sev.vue'
import Icon from '../components/Icon.vue'
import AuditTabs from '../components/AuditTabs.vue'
// three.js 는 이 그래프를 그릴 때만 내려받는다
const RiskGraph3D = defineAsyncComponent(() => import('../components/RiskGraph3D.vue'))
const id = useRoute().params.id as string
const rows = ref<RiskPath[]>([])
const findings = ref<Record<string, Finding>>({})
const loaded = ref(false)
const focus = ref<string | null>(null)
onMounted(async () => {
  const [paths, fs] = await Promise.all([get<RiskPath[]>(`/audits/${id}/risk-paths`), get<Finding[]>(`/audits/${id}/findings`)])
  findings.value = Object.fromEntries(fs.map((f) => [f.id, f]))
  rows.value = paths; loaded.value = true
})
</script>
<template>
  <AuditTabs :id="id" />
  <div v-if="loaded && !rows.length" class="card empty"><Icon name="check-circle" /><b>결합 위험이 없습니다</b><span>여러 Finding 이 엮인 위험 경로가 발견되지 않았습니다.</span></div>
  <div v-if="rows.length" class="card mb">
    <div class="card-head"><h3><Icon name="share" :size="16" />위험 경로 그래프</h3><span class="muted small">아래 경로에 마우스를 올리면 해당 경로만 강조됩니다</span></div>
    <RiskGraph3D :paths="rows" :findings="findings" :focus="focus" />
  </div>
  <div class="stack" style="gap:14px">
    <div v-for="r in rows" :key="r.id" class="card risk" :class="{ on: focus === r.id }" :style="{ '--c': `var(--${r.severity.toLowerCase()})` }" @mouseenter="focus = r.id" @mouseleave="focus = null">
      <div class="row"><Sev :v="r.severity" /><b style="font-size:15px">{{ r.title }}</b><span class="spacer" /><span class="badge mono">{{ r.rule_id }}</span></div>
      <p class="mt-s" style="color:var(--text-2)">{{ r.explanation }}</p>
      <div class="row small"><span class="muted">구성 Finding</span>
        <template v-for="(f, i) in r.finding_ids" :key="f">
          <Icon v-if="i" name="chevron" :size="13" class="muted" />
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
