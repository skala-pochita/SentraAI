<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { get, post } from '../api/client'
interface Dep { status: string; error?: string }
interface Dlq { id: string; original_topic: string; error_code: string; error_message: string; status: string; created_at: string }
const health = ref<{ status: string; dependencies: Record<string, Dep> } | null>(null)
const dlq = ref<{ items?: Dlq[] } | Dlq[]>([])
const items = () => (Array.isArray(dlq.value) ? dlq.value : dlq.value.items ?? [])
const load = async () => { health.value = await get('/ops/health?deep=true'); dlq.value = await get('/ops/dlq') }
onMounted(load)
async function replay(m: Dlq) { await post(`/ops/dlq/${m.id}/replay`); await load() }
</script>
<template>
  <div class="card"><h3>의존성 상태 <span class="status" :class="health?.status === 'ok' ? 'COMPLETED' : 'FAILED'">{{ health?.status }}</span></h3>
    <div class="row"><span v-for="(d, k) in health?.dependencies" :key="k" class="badge" :class="d.status === 'ok' ? '' : 'sev-CRITICAL'">{{ k }}: {{ d.status }}</span></div></div>
  <div class="card mt"><h3>DLQ</h3><p v-if="!items().length" class="muted">비어 있습니다.</p>
    <table v-else><tr><th>토픽</th><th>오류</th><th>상태</th><th>시각</th><th /></tr>
      <tr v-for="m in items()" :key="m.id"><td>{{ m.original_topic }}</td><td>{{ m.error_code }} {{ m.error_message }}</td><td>{{ m.status }}</td><td>{{ m.created_at?.slice(0, 16) }}</td>
        <td><button class="btn" :disabled="m.status === 'REPLAYED'" @click="replay(m)">재처리</button></td></tr></table></div>
</template>
