<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { get, put } from '../api/client'

interface Criterion { name: string; met: boolean | null; detail: string }
interface Settings {
  mode: 'mock' | 'live'; environment: Record<string, Record<string, string>>; model: string
  max_requests: number; max_total_tokens: number; max_output_tokens: number
  base_url_variable: string; api_key_variable: string; model_variable: string; mode_variable: string
  live_mode_value: string; mock_mode_value: string
  secret_names: string[]; provider_key_configured: boolean; allowed_models: string[]
  readiness: { ready: boolean; criteria: Criterion[]; services: string[]; verification: string }
}
const props = defineProps<{ targetId: string }>()
const emit = defineEmits<{ ready: [boolean]; dirty: [] }>()
const settings = ref<Settings | null>(null)
const envText = ref('{}')
const providerKey = ref('')
const secretRows = ref<{ service: string; name: string; value: string }[]>([])
const removeSecrets = ref<string[]>([])
const removeProvider = ref(false)
const busy = ref(false)
const dirty = ref(false)
const error = ref('')
let generation = 0
async function load() {
  const current = ++generation
  error.value = ''; settings.value = null; emit('ready', false)
  try {
    const loaded = await get<Settings>(`/targets/${props.targetId}/execution`)
    if (current !== generation) return
    settings.value = loaded
    envText.value = JSON.stringify(settings.value.environment, null, 2)
    providerKey.value = ''; secretRows.value = []; removeSecrets.value = []; removeProvider.value = false
    dirty.value = false; emit('ready', settings.value.readiness.ready)
  } catch (e) { error.value = (e as Error).message }
}
onMounted(load)
watch(() => props.targetId, load)
function changed() { dirty.value = true; emit('dirty'); emit('ready', false) }
function remove(name: string) { removeSecrets.value.push(name); changed() }
async function save() {
  if (!settings.value) return
  busy.value = true; error.value = ''
  try {
    let environment: unknown
    try { environment = JSON.parse(envText.value) } catch { throw new Error('일반 환경변수를 JSON 형식으로 입력하세요.') }
    const secrets: Record<string, Record<string, string>> = {}
    for (const row of secretRows.value) {
      if (row.value) (secrets[row.service] ??= {})[row.name] = row.value
    }
    const s = settings.value
    await put(`/targets/${props.targetId}/execution`, {
      mode: s.mode, environment, model: s.model, max_requests: s.max_requests,
      max_total_tokens: s.max_total_tokens, max_output_tokens: s.max_output_tokens,
      base_url_variable: s.base_url_variable, api_key_variable: s.api_key_variable,
      model_variable: s.model_variable, mode_variable: s.mode_variable,
      live_mode_value: s.live_mode_value, mock_mode_value: s.mock_mode_value,
      secrets, remove_secrets: removeSecrets.value, remove_provider_key: removeProvider.value,
      ...(providerKey.value ? { provider_api_key: providerKey.value } : {}),
    })
    await load()
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
</script>
<template>
  <section class="card mt" aria-label="프로젝트 실행 설정">
    <h3>프로젝트 실행 설정</h3>
    <p class="muted small">일반 설정과 비밀값을 분리합니다. 제공자 키는 암호화 저장하고, 타깃에는 감사 전용 임시 토큰만 전달합니다.</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <form v-if="settings" @submit.prevent="save" @input="changed" @change="changed">
      <div class="row">
        <label><input v-model="settings.mode" type="radio" value="mock" /> 모의 에이전트 검증</label>
        <label><input v-model="settings.mode" type="radio" value="live" /> 실제 LLM 공격 검증</label>
      </div>
      <p class="muted small">실제 검증에서는 모델 호출 비용이 발생합니다. 아래 요청·토큰 한도로 사용량을 제한합니다.</p>
      <div v-if="settings.mode === 'live'" class="stack mt" style="gap:12px">
        <label>허용 모델 <select v-model="settings.model"><option v-for="m in settings.allowed_models" :key="m" :value="m">{{ m }}</option></select></label>
        <label>검사용 제공자 API 키 <input v-model="providerKey" type="password" autocomplete="new-password" :placeholder="settings.provider_key_configured ? '저장됨 · 변경할 때만 입력' : '검사용 API 키 입력'" /></label>
        <label v-if="settings.provider_key_configured"><input v-model="removeProvider" type="checkbox" /> 저장된 제공자 키 삭제</label>
        <div class="grid cols-3">
          <label>최대 요청 수<input v-model.number="settings.max_requests" type="number" min="1" max="100" /></label>
          <label>총 토큰 한도<input v-model.number="settings.max_total_tokens" type="number" min="1000" max="2000000" /></label>
          <label>응답당 토큰 한도<input v-model.number="settings.max_output_tokens" type="number" min="128" max="8192" /></label>
        </div>
        <details><summary>에이전트가 사용하는 모델 환경변수 이름</summary>
          <p class="muted small">프로젝트 코드가 읽는 이름과 일치해야 합니다. OpenAI SDK라면 OPENAI_BASE_URL / OPENAI_API_KEY를 지정할 수 있습니다. 현재 프록시는 비스트리밍 Chat Completions만 지원합니다.</p>
          <div class="grid cols-2">
            <label>모델 URL 변수<input v-model="settings.base_url_variable" /></label>
            <label>인증 키 변수<input v-model="settings.api_key_variable" /></label>
            <label>모델명 변수<input v-model="settings.model_variable" /></label>
            <label>모드 변수<input v-model="settings.mode_variable" /></label>
            <label>실제 모드 값<input v-model="settings.live_mode_value" /></label>
            <label>모의 모드 값<input v-model="settings.mock_mode_value" /></label>
          </div>
        </details>
      </div>
      <label class="stack mt">일반 환경변수 (서비스별 JSON)
        <textarea v-model="envText" class="mono" rows="5" spellcheck="false" aria-label="일반 환경변수 JSON" />
      </label>
      <p class="muted small">예: {"agent":{"BACKEND_URL":"http://backend:8080"}} · 키·토큰·비밀번호는 아래 비밀값에 입력하세요.</p>
      <details class="mt"><summary>테스트용 비밀 환경변수</summary>
        <p class="muted small">운영 데이터 대신 검사용 계정·데이터를 사용하세요. 이 값은 실행 중 해당 서비스에 전달됩니다. LLM 제공자 키는 위 전용 입력란을 사용하세요.</p>
        <div v-for="name in settings.secret_names" :key="name" class="row mt-s">
          <span>{{ name }} · {{ removeSecrets.includes(name) ? '삭제 예정' : '저장됨' }}</span>
          <button type="button" class="btn sm" :disabled="removeSecrets.includes(name)" @click="remove(name)">삭제</button>
        </div>
        <div v-for="(row, i) in secretRows" :key="i" class="grid cols-3 mt-s">
          <input v-model="row.service" placeholder="서비스 (예: backend)" aria-label="비밀값 서비스" />
          <input v-model="row.name" placeholder="변수명 (예: DB_PASSWORD)" aria-label="비밀 환경변수 이름" />
          <input v-model="row.value" type="password" autocomplete="new-password" placeholder="비밀값" aria-label="비밀 환경변수 값" />
        </div>
        <button type="button" class="btn sm mt" @click="secretRows.push({ service: 'agent', name: '', value: '' }); changed()">비밀값 추가</button>
      </details>
      <div class="row mt"><button class="btn primary" type="submit" :disabled="busy">{{ busy ? '저장 중…' : '설정 저장·조건 확인' }}</button><span v-if="dirty" class="muted small">변경한 설정을 저장하세요.</span></div>
    </form>
    <div v-if="settings" class="mt">
      <h3>전체 검사 준비 조건</h3>
      <div v-for="c in settings.readiness.criteria" :key="c.name" class="row mt-s" style="align-items:flex-start">
        <span class="badge" :class="c.met === false ? 'bad' : ''">{{ c.met === true ? '확인' : c.met === false ? '보완 필요' : '실행 시 확인' }}</span>
        <div><b>{{ c.name }}</b><p class="muted small" style="margin:3px 0">{{ c.detail }}</p></div>
      </div>
      <p class="muted small">{{ settings.readiness.verification }} 실행·모델 연결 실패는 안전 판정으로 처리하지 않습니다.</p>
    </div>
  </section>
</template>
