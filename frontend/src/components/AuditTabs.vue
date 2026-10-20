<script setup lang="ts">
// 감사 하위 화면(진행·Findings·결합 위험·레드팀·보고서) 사이 이동
import Icon from './Icon.vue'
import { onMounted, ref } from 'vue'
import { get } from '../api/client'
const props = defineProps<{ id: string; completed?: boolean }>()
const ready = ref(false)
onMounted(async () => { ready.value = (await get<{ status: string }>(`/audits/${props.id}`)).status === 'COMPLETED' })
const TABS = [
  { s: '', label: '진행', icon: 'radio' },
  { s: '/findings', label: 'Findings', icon: 'bug' },
  { s: '/risks', label: '결합 위험', icon: 'share' },
  { s: '/redteam', label: '레드팀', icon: 'crosshair' },
  { s: '/report', label: '보고서', icon: 'file' },
]
</script>
<template>
  <nav class="tabs" aria-label="감사 화면">
    <RouterLink v-for="t in TABS.filter((t) => !t.s || completed || ready)" :key="t.s" :to="`/audits/${id}${t.s}`"><Icon :name="t.icon" :size="15" />{{ t.label }}</RouterLink>
  </nav>
</template>
