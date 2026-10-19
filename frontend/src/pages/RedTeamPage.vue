<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { get } from '../api/client'
interface Run { id: string; attempt: number; success: boolean; reply_masked: string; tool_calls: unknown[]; judged_by: string }
interface Row { probe_id: string; category: string; channel: string; runs: Run[] }
const id = useRoute().params.id as string
const data = ref<{ probes: Row[]; asr_by_category: Record<string, number> } | null>(null)
const open = ref<Run | null>(null)
onMounted(async () => { data.value = await get(`/audits/${id}/probes`) })
</script>
<template>
  <div v-if="data">
    <div class="card"><h3>카테고리별 공격 성공률(ASR)</h3><div class="row"><span v-for="(v, k) in data.asr_by_category" :key="k" class="badge">{{ k }} {{ Math.round(v * 100) }}%</span></div></div>
    <div class="card mt"><table><tr><th>Probe</th><th>카테고리</th><th>채널</th><th>시도 (클릭 → 대화)</th></tr>
      <tr v-for="p in data.probes" :key="p.probe_id"><td>{{ p.probe_id }}</td><td>{{ p.category }}</td><td>{{ p.channel }}</td>
        <td><button v-for="r in p.runs" :key="r.id" class="btn cell" :class="r.success ? 'bad' : 'ok'" @click="open = r">{{ r.success ? '성공' : '차단' }}</button></td></tr></table></div>
    <div v-if="open" class="card mt"><b>응답 ({{ open.judged_by }} 판정)</b><pre>{{ open.reply_masked }}</pre><pre>{{ JSON.stringify(open.tool_calls, null, 1) }}</pre></div>
  </div>
</template>
