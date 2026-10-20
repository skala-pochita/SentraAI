<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { get } from '../api/client'
import type { RiskPath } from '../api/types'
import Sev from '../components/Sev.vue'
import Icon from '../components/Icon.vue'
import AuditTabs from '../components/AuditTabs.vue'
const id = useRoute().params.id as string
const rows = ref<RiskPath[]>([])
const loaded = ref(false)
onMounted(async () => { rows.value = await get<RiskPath[]>(`/audits/${id}/risk-paths`); loaded.value = true })
</script>
<template>
  <AuditTabs :id="id" />
  <div v-if="loaded && !rows.length" class="card empty"><Icon name="check-circle" /><b>결합 위험이 없습니다</b><span>여러 Finding 이 엮인 위험 경로가 발견되지 않았습니다.</span></div>
  <div class="stack" style="gap:14px">
    <div v-for="r in rows" :key="r.id" class="card risk" :style="{ '--c': `var(--${r.severity.toLowerCase()})` }">
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
.risk { border-left: 3px solid var(--c); }
.spacer { flex: 1; }
</style>
