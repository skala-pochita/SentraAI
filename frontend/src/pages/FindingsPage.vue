<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { get } from '../api/client'
import type { Finding } from '../api/types'
import Sev from '../components/Sev.vue'
import Icon from '../components/Icon.vue'
import AuditTabs from '../components/AuditTabs.vue'
const id = useRoute().params.id as string
const rows = ref<Finding[]>([])
const loaded = ref(false)
const sev = ref('')
const q = ref('')
const SEVS = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO']
onMounted(async () => { rows.value = await get<Finding[]>(`/audits/${id}/findings`); loaded.value = true })
const shown = computed(() => rows.value.filter((f) => (!sev.value || f.severity === sev.value) && (!q.value || (f.title + f.category).toLowerCase().includes(q.value.toLowerCase()))))
const count = (s: string) => rows.value.filter((f) => f.severity === s).length
</script>
<template>
  <AuditTabs :id="id" />
  <div class="row mb" style="justify-content:space-between">
    <div class="chips" role="group" aria-label="심각도 필터">
      <button class="chip-btn" :class="{ on: !sev }" @click="sev = ''">전체 <span class="n">{{ rows.length }}</span></button>
      <button v-for="s in SEVS" :key="s" class="chip-btn" :class="{ on: sev === s }" :disabled="!count(s)" @click="sev = s">
        <span class="sev" :class="`sev-${s}`" style="padding:2px 6px; border:0; background:none">{{ s }}</span><span class="n">{{ count(s) }}</span>
      </button>
    </div>
    <div class="row">
      <span class="input-icon" style="width:260px"><Icon name="search" :size="16" /><input v-model="q" placeholder="제목·카테고리 검색" aria-label="검색" /></span>
      <span class="muted small num">{{ shown.length }} / {{ rows.length }}</span>
    </div>
  </div>
  <div class="card flush">
    <div v-if="shown.length" class="table-wrap"><table>
      <thead><tr><th>심각도</th><th>카테고리</th><th>제목</th><th>출처</th><th>신뢰도</th><th>상태</th></tr></thead>
      <tbody><tr v-for="f in shown" :key="f.id">
        <td><Sev :v="f.severity" /></td>
        <td><span class="badge mono">{{ f.category }}</span></td>
        <td><RouterLink :to="`/findings/${f.id}`">{{ f.title }}</RouterLink></td>
        <td class="muted">{{ f.source }}</td>
        <td><div class="meter"><div class="progress"><span :style="{ width: `${Math.round(f.confidence * 100)}%` }" /></div><span class="v">{{ f.confidence }}</span></div></td>
        <td><span class="status" :class="f.status === 'OPEN' ? 'WAITING_APPROVAL' : f.status === 'RESOLVED' ? 'COMPLETED' : ''">{{ f.status }}</span></td>
      </tr></tbody>
    </table></div>
    <div v-else-if="loaded" class="empty"><Icon name="check-circle" /><b>조건에 맞는 Finding 이 없습니다</b></div>
  </div>
</template>
