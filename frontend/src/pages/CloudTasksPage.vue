<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, get, ApiError } from '../api/client'

interface Row { taskId: string; status: string; createdAt: string; updatedAt: string }
const router = useRouter()
const rows = ref<Row[]>([])
const text = ref('')
const error = ref('')
const busy = ref(false)
const load = async () => { rows.value = await get<Row[]>('/../ai/tasks?mine=true') }
onMounted(load)
function pick(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (f) void f.text().then((t) => { text.value = t })
}
async function submit() {
  error.value = ''; busy.value = true
  try {
    const bundle = JSON.parse(text.value)
    const r = await api<{ taskId: string }>('/../ai/tasks', { method: 'POST', body: JSON.stringify({ evidence_bundle: bundle }) })
    router.push(`/cloud/${r.taskId}`)  // 202 는 접수일 뿐이다
  } catch (e) { error.value = e instanceof ApiError ? `${e.body.message} ${JSON.stringify(e.body.details ?? '')}` : '올바른 JSON 이 아닙니다' } finally { busy.value = false }
}
</script>
<template>
  <div class="grid cols-2">
    <div class="card"><h3>증거 묶음 제출</h3>
      <p class="muted">정규화된 evidence_bundle.json 을 올립니다. 결함·등급 같은 판정 필드가 있으면 거부됩니다.</p>
      <input type="file" accept="application/json" @change="pick" /><textarea v-model="text" rows="10" class="mt" placeholder="{ &quot;schema_version&quot;: &quot;1&quot;, ... }" />
      <p v-if="error" class="error">{{ error }}</p><button class="btn primary mt" :disabled="!text || busy" @click="submit">감사 요청</button></div>
    <div class="card"><h3>내 작업</h3><table><tr><th>작업</th><th>상태</th><th>생성</th></tr>
      <tr v-for="r in rows" :key="r.taskId"><td><RouterLink :to="`/cloud/${r.taskId}`">{{ r.taskId.slice(0, 8) }}</RouterLink></td>
        <td><span class="status" :class="r.status">{{ r.status }}</span></td><td class="muted">{{ r.createdAt?.slice(0, 16) }}</td></tr></table></div>
  </div>
</template>
