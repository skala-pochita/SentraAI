<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { get, post, ApiError } from '../api/client'
import type { Paged, Target } from '../api/types'
import Icon from '../components/Icon.vue'

const router = useRouter()
const targets = ref<Target[]>([])
const selected = ref<string[]>([])
const mode = ref('full')
const error = ref('')
const busy = ref(false)
const MODES = [
  { v: 'full', label: '전체', desc: '코드 + 에이전트', icon: 'layers' },
  { v: 'code', label: '코드', desc: '정적 규칙 + LLM 조사', icon: 'code' },
  { v: 'agent', label: '에이전트', desc: '레드팀 probe', icon: 'bot' },
]
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
  <div class="card" style="max-width:820px">
    <p class="section-title"><Icon name="layers" :size="14" />1. 감사 대상</p>
    <div class="grid cols-2">
      <label v-for="t in targets" :key="t.id" class="pick" :class="{ on: selected.includes(t.id) }">
        <input type="checkbox" :value="t.id" v-model="selected" />
        <span class="pick-ico"><Icon :name="t.type === 'repo' ? 'code' : 'bot'" :size="20" /></span>
        <span style="min-width:0"><b>{{ t.name }}</b><span class="muted small mono" style="display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap">{{ t.ref }}</span></span>
        <span class="badge" style="margin-left:auto">{{ t.type }}</span>
      </label>
    </div>
    <p v-if="!targets.length" class="muted">등록된 대상이 없습니다. <span class="mono">make seed</span> 로 샘플 대상을 넣으세요.</p>

    <p class="section-title mt" style="margin-top:24px"><Icon name="zap" :size="14" />2. 감사 모드</p>
    <div class="grid cols-3" role="radiogroup" aria-label="감사 모드">
      <label v-for="m in MODES" :key="m.v" class="pick" :class="{ on: mode === m.v }">
        <input type="radio" name="mode" :value="m.v" v-model="mode" />
        <span class="pick-ico"><Icon :name="m.icon" :size="18" /></span>
        <span><b>{{ m.label }}</b><span class="muted small" style="display:block">{{ m.desc }}</span></span>
      </label>
    </div>

    <p v-if="error" class="error mt"><Icon name="alert" :size="16" />{{ error }}</p>
    <div class="row" style="margin-top:24px; justify-content:space-between">
      <span class="muted small">대상 {{ selected.length }}개 선택 · CRITICAL 수정안은 ADMIN 승인 후에만 반영됩니다.</span>
      <button class="btn primary lg" :disabled="busy || !selected.length" @click="start"><Icon name="play" :size="16" />{{ busy ? '시작 중…' : '감사 시작' }}</button>
    </div>
  </div>
</template>

<style scoped>
.pick {
  display: flex; align-items: center; gap: 12px; padding: 14px; border-radius: 14px; cursor: pointer; position: relative;
  border: 1px solid var(--border); background: var(--surface-2); transition: border-color .15s, background .15s, box-shadow .2s;
}
.pick:hover { border-color: var(--border-strong); }
.pick.on { border-color: color-mix(in srgb, var(--accent) 55%, transparent); background: var(--accent-soft); box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 12%, transparent); }
.pick input { position: absolute; opacity: 0; pointer-events: none; }
.pick:has(input:focus-visible) { outline: 2px solid var(--accent); outline-offset: 2px; }
.pick-ico { width: 40px; height: 40px; border-radius: 11px; display: grid; place-items: center; flex: none; color: var(--muted); background: var(--surface-3); transition: all .15s; }
.pick.on .pick-ico { color: var(--accent-ink); background: var(--accent); }
.pick b { color: var(--text); font-weight: 600; }
</style>
