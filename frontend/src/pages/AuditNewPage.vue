<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { get, post, ApiError } from '../api/client'
import type { Paged, Target } from '../api/types'

const router = useRouter()
const targets = ref<Target[]>([])
const selected = ref<string[]>([])
const mode = ref('full')
const error = ref('')
const busy = ref(false)
onMounted(async () => {
  targets.value = (await get<Paged<Target>>('/targets')).items
  selected.value = targets.value.map((t) => t.id)
})
async function start() {
  busy.value = true; error.value = ''
  try {
    const r = await post<{ audit_id: string }>('/audits', { target_ids: selected.value, mode: mode.value })
    router.push(`/audits/${r.audit_id}`)
  } catch (e) { error.value = e instanceof ApiError ? e.body.message : String(e) } finally { busy.value = false }
}
</script>
<template>
  <div class="card" style="max-width:560px">
    <h3>감사 실행</h3>
    <div v-for="t in targets" :key="t.id"><label><input type="checkbox" :value="t.id" v-model="selected" style="width:auto" /> {{ t.name }} <span class="badge">{{ t.type }}</span></label></div>
    <label class="mt" style="display:block">모드
      <select v-model="mode"><option value="full">전체 (코드 + 에이전트)</option><option value="code">코드</option><option value="agent">에이전트</option></select>
    </label>
    <p v-if="error" class="error">{{ error }}</p>
    <button class="btn primary mt" :disabled="busy || !selected.length" @click="start">감사 시작</button>
  </div>
</template>
