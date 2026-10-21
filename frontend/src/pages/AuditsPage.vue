<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { get } from '../api/client'
import type { Audit, Paged } from '../api/types'
import Icon from '../components/Icon.vue'
import Sev from '../components/Sev.vue'
const rows = ref<Audit[]>([])
const loaded = ref(false)
const filter = ref('')
onMounted(async () => { rows.value = (await get<Paged<Audit>>('/audits?size=100')).items; loaded.value = true })
const statuses = computed(() => [...new Set(rows.value.map((a) => a.status))])
const shown = computed(() => rows.value.filter((a) => !filter.value || a.status === filter.value))
const count = (s: string) => rows.value.filter((a) => a.status === s).length
const ORDER = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO']
const sevs = (a: Audit) => ORDER.filter((k) => a.summary?.by_severity?.[k]).map((k) => [k, a.summary!.by_severity![k]] as const)
</script>
<template>
  <div class="chips mb">
    <button class="chip-btn" :class="{ on: !filter }" @click="filter = ''">전체 <span class="n">{{ rows.length }}</span></button>
    <button v-for="s in statuses" :key="s" class="chip-btn" :class="{ on: filter === s }" @click="filter = s">{{ s }} <span class="n">{{ count(s) }}</span></button>
  </div>
  <div class="card flush">
    <div v-if="shown.length" class="table-wrap"><table>
      <thead><tr><th>ID</th><th>상태</th><th>모드</th><th>심각도</th><th class="r">Finding</th><th>시작</th><th /></tr></thead>
      <tbody><tr v-for="a in shown" :key="a.id">
        <td><RouterLink class="id-link" :to="`/audits/${a.id}`">{{ a.id.slice(0, 8) }}</RouterLink></td>
        <td><span class="status" :class="a.status">{{ a.status }}</span></td>
        <td><span class="badge">{{ a.mode }}</span></td>
        <td><div class="chips"><Sev v-for="[k, v] in sevs(a)" :key="k" :v="k" :n="v" /><span v-if="!sevs(a).length" class="muted">-</span></div></td>
        <td class="r num">{{ a.summary?.total ?? '-' }}</td>
        <td class="muted mono">{{ a.created_at?.slice(0, 16).replace('T', ' ') }}</td>
        <td class="r"><RouterLink :to="`/audits/${a.id}`" class="btn ghost sm" :aria-label="`감사 ${a.id.slice(0, 8)} 열기`"><Icon name="chevron" :size="15" /></RouterLink></td>
      </tr></tbody>
    </table></div>
    <div v-else-if="loaded" class="empty"><Icon name="inbox" /><b>감사가 없습니다</b></div>
  </div>
</template>
