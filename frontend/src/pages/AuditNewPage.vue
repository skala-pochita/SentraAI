<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, get, post, ApiError } from '../api/client'
import type { Paged, Target } from '../api/types'
import Icon from '../components/Icon.vue'
import ExecutionSettingsPanel from '../components/ExecutionSettingsPanel.vue'

const router = useRouter()
const targets = ref<Target[]>([])
const selected = ref<string[]>([])
const mode = ref('code')
const uploading = ref(false)
const ready = ref(false)
const project = computed(() => {
  const repo = targets.value.find((t) => selected.value.includes(t.id) && t.type === 'repo' && t.config?.runtime)
  if (repo) return repo
  const agent = targets.value.find((t) => selected.value.includes(t.id) && t.type === 'agent')
  return agent?.linked_target_id ? targets.value.find((t) => t.id === agent.linked_target_id && t.config?.runtime) : undefined
})
async function upload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  uploading.value = true; error.value = ''
  try {
    const form = new FormData(); form.append('file', file)
    const target = await api<Target>('/targets/upload', { method: 'POST', body: form })
    targets.value = (await get<Paged<Target>>('/targets?size=200')).items
    selected.value = [target.id, ...(target.linked_target_id ? [target.linked_target_id] : [])]
    mode.value = target.linked_target_id ? 'full' : 'code'
  } catch (e) { error.value = e instanceof ApiError ? e.body.message : String(e) }
  finally { uploading.value = false; input.value = '' }
}
const error = ref('')
const busy = ref(false)
const MODES = [
  { v: 'full', label: '전체', desc: '코드 + 에이전트', icon: 'layers' },
  { v: 'code', label: '코드', desc: '정적 규칙 + LLM 조사', icon: 'code' },
  { v: 'agent', label: '에이전트', desc: '레드팀 probe', icon: 'bot' },
]
onMounted(async () => {
  targets.value = (await get<Paged<Target>>('/targets')).items
  selected.value = []
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
    <details class="mb" open>
      <summary><b>전체 검사를 위한 ZIP 프로젝트 기준</b></summary>
      <ol class="muted small" style="line-height:1.9">
        <li>ZIP 20MiB 이하, 압축 해제 100MiB / 5,000개 항목 이하. 절대 경로·상위 경로·심볼릭 링크는 허용하지 않습니다.</li>
        <li>소스와 의존성 파일을 포함하세요. .venv, node_modules, 빌드 산출물, 실제 키가 든 .env는 제외하세요.</li>
        <li>프로젝트 루트에 Compose YAML, 로컬 Dockerfile, 서비스 1~3개가 필요합니다. agent 또는 target-agent 서비스와 내부 포트를 명시하세요.</li>
        <li>manifest.yaml의 tools 또는 Python 리터럴 TOOL_SCHEMAS/TOOLS로 에이전트 도구를 정의하세요. 권한·승인 정책과 시스템 프롬프트도 제공하면 검증 범위가 넓어집니다.</li>
        <li>GET /health(200), POST /chat이 필요합니다. Atlas 형식은 POST /sessions도 필요합니다. 다른 호환 프로젝트는 audit-runtime.yaml과 audit-probes.yaml을 제공하세요.</li>
        <li>프로젝트가 비루트·읽기 전용 파일시스템에서 실행되어야 합니다. 쓰기는 /tmp로 제한하고 운영 DB·외부 업무 시스템은 테스트 대역으로 바꾸세요.</li>
        <li>실제 LLM 검증은 OpenAI 호환 비스트리밍 Chat Completions를 사용하고, 모델 주소·키 환경변수를 바꿀 수 있어야 합니다. 업로드 후 실행 설정에서 검사용 키를 등록하세요.</li>
      </ol>
      <p class="muted small">ZIP만으로 모든 프로젝트를 실행할 수 있는 것은 아닙니다. 아래에서 실제 프로젝트의 준비 조건을 확인하고 보완하세요. 모의(mock) 실행은 실제 LLM 방어 성능 검증과 구분됩니다.</p>
      <details><summary>실행 계약 예시</summary><pre>project/
  compose.yaml
  agent/Dockerfile
  agent/app/
  manifest.yaml
  system_prompt.md
  audit-runtime.yaml   # protocol: auditor 또는 atlas
  audit-probes.yaml    # 프로젝트별 공격·성공 조건
  backend/            # 필요한 의존 서비스 소스</pre></details>
    </details>
    <p class="section-title">프로젝트 업로드</p>
    <p class="muted small">소스 프로젝트 ZIP · 최대 20MB, 압축 해제 100MB / 5,000개 항목. 원본은 변경하지 않습니다. 에이전트가 발견되면 권한 검사와 격리 실행·공격 검사를 함께 선택합니다.</p>
    <input type="file" accept=".zip,application/zip" :disabled="uploading || busy" aria-label="프로젝트 ZIP 업로드" @change="upload" />
    <p v-if="uploading" class="muted">업로드 중…</p>
    <p class="section-title mt"><Icon name="layers" :size="14" />1. 감사 대상</p>
    <div class="grid cols-2">
      <label v-for="t in targets" :key="t.id" class="pick" :class="{ on: selected.includes(t.id) }">
        <input type="checkbox" :value="t.id" v-model="selected" />
        <span class="pick-ico"><Icon :name="t.type === 'repo' ? 'code' : 'bot'" :size="20" /></span>
        <span style="min-width:0"><b>{{ t.name }}</b><span class="muted small mono" style="display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap">{{ t.ref || '업로드 프로젝트 에이전트' }}</span><span v-if="t.type === 'repo' && t.config?.runtime" class="muted small" style="display:block">{{ t.config.agent_detected ? '에이전트 정의 발견 · 실행 설정에서 준비 조건 확인' : '소스 검사 대상' }}</span></span>
        <span class="badge" style="margin-left:auto">{{ t.type }}</span>
      </label>
    </div>
    <p v-if="!targets.length" class="muted">등록된 대상이 없습니다. 프로젝트 ZIP을 업로드하세요.</p>

    <p class="section-title mt" style="margin-top:24px"><Icon name="zap" :size="14" />2. 감사 모드</p>
    <div class="grid cols-3" role="radiogroup" aria-label="감사 모드">
      <label v-for="m in MODES" :key="m.v" class="pick" :class="{ on: mode === m.v }">
        <input type="radio" name="mode" :value="m.v" v-model="mode" />
        <span class="pick-ico"><Icon :name="m.icon" :size="18" /></span>
        <span><b>{{ m.label }}</b><span class="muted small" style="display:block">{{ m.desc }}</span></span>
      </label>
    </div>

    <ExecutionSettingsPanel v-if="project" :key="project.id" :target-id="project.id" @ready="ready = $event" />
    <p v-if="project && mode !== 'code' && !ready" class="muted mt">실행 설정을 저장하고 전체 검사 준비 조건을 충족해 주세요. 코드 검사는 별도로 실행할 수 있습니다.</p>
    <p v-if="error" class="error mt"><Icon name="alert" :size="16" />{{ error }}</p>
    <div class="row" style="margin-top:24px; justify-content:space-between">
      <span class="muted small">대상 {{ selected.length }}개 선택 · 현재 코드의 문제점과 권장 조치를 제공합니다.</span>
      <button class="btn primary lg" :disabled="busy || uploading || !selected.length || (!!project && mode !== 'code' && !ready)" @click="start"><Icon name="play" :size="16" />{{ busy ? '시작 중…' : '감사 시작' }}</button>
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
