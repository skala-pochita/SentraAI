<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { get, post } from '../api/client'
import Icon from '../components/Icon.vue'
interface Dep { status: string; error?: string }
interface Dlq { id: string; original_topic: string; error_code: string; error_message: string; status: string; created_at: string }
const health = ref<{ status: string; dependencies: Record<string, Dep> } | null>(null)
const dlq = ref<{ items?: Dlq[] } | Dlq[]>([])
const busy = ref(false)
const items = () => (Array.isArray(dlq.value) ? dlq.value : dlq.value.items ?? [])
const load = async () => { busy.value = true; try { health.value = await get('/ops/health?deep=true'); dlq.value = await get('/ops/dlq') } finally { busy.value = false } }
onMounted(load)
async function replay(m: Dlq) { await post(`/ops/dlq/${m.id}/replay`); await load() }
const DEP_ICON: Record<string, string> = { db: 'layers', redis: 'zap', kafka: 'share', 'business-mcp': 'server', 'rag-mcp': 'book', 'target-agent': 'bot' }
</script>
<template>
  <div class="card mb">
    <div class="card-head">
      <h3><Icon name="server" :size="16" />의존성 상태</h3>
      <span class="status" :class="health?.status === 'ok' ? 'COMPLETED' : 'FAILED'">{{ health?.status ?? '확인 중' }}</span>
      <span class="spacer" style="flex:1" />
      <button class="btn sm" :disabled="busy" @click="load"><Icon name="refresh" :size="14" />다시 확인</button>
    </div>
    <div class="grid cols-3">
      <div v-for="(d, k) in health?.dependencies" :key="k" class="dep" :class="d.status === 'ok' ? 'ok' : 'bad'">
        <span class="dep-ico"><Icon :name="DEP_ICON[k] ?? 'server'" :size="18" /></span>
        <span style="min-width:0"><b class="mono">{{ k }}</b><span class="small" style="display:block; overflow-wrap:anywhere">{{ d.status === 'ok' ? '정상' : d.error ?? d.status }}</span></span>
        <span class="status" :class="d.status === 'ok' ? 'ok' : 'bad'" style="margin-left:auto">{{ d.status }}</span>
      </div>
    </div>
  </div>
  <div class="card flush">
    <div class="card-head"><h3><Icon name="inbox" :size="16" />DLQ</h3><span class="sub">재시도 3회 후에도 실패한 메시지</span></div>
    <div v-if="!items().length" class="empty"><Icon name="check-circle" /><b>비어 있습니다</b></div>
    <div v-else class="table-wrap mt-s"><table>
      <thead><tr><th>토픽</th><th>오류</th><th>상태</th><th>시각</th><th /></tr></thead>
      <tbody><tr v-for="m in items()" :key="m.id">
        <td class="mono">{{ m.original_topic }}</td>
        <td><span class="badge tone bad mono">{{ m.error_code }}</span> <span class="small">{{ m.error_message }}</span></td>
        <td><span class="status" :class="m.status === 'REPLAYED' ? 'COMPLETED' : 'WAITING_APPROVAL'">{{ m.status }}</span></td>
        <td class="muted mono">{{ m.created_at?.slice(0, 16).replace('T', ' ') }}</td>
        <td class="r"><button class="btn sm" :disabled="m.status === 'REPLAYED'" @click="replay(m)"><Icon name="refresh" :size="14" />재처리</button></td>
      </tr></tbody>
    </table></div>
  </div>
</template>
<style scoped>
.dep { --c: var(--ok); display: flex; align-items: center; gap: 12px; padding: 14px; border-radius: 12px; background: var(--surface-2); border: 1px solid var(--border); color: var(--muted); min-width: 0; }
.dep.bad { --c: var(--critical); border-color: color-mix(in srgb, var(--critical) 35%, var(--border)); }
.dep b { color: var(--text); font-weight: 600; }
.dep-ico { width: 38px; height: 38px; flex: none; border-radius: 10px; display: grid; place-items: center; color: var(--c); background: color-mix(in srgb, var(--c) 12%, transparent); }
</style>
