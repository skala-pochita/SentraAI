<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuth } from '../stores/auth'
import { ApiError } from '../api/client'
import Icon from '../components/Icon.vue'
import ThemeToggle from '../components/ThemeToggle.vue'

const email = ref('admin@demo.io')
const password = ref('')
const error = ref('')
const busy = ref(false)
const auth = useAuth()
const router = useRouter()
const route = useRoute()

async function submit() {
  error.value = ''
  busy.value = true
  try {
    await auth.login(email.value, password.value)
    router.push((route.query.next as string) || '/')
  } catch (e) {
    error.value = e instanceof ApiError ? e.body.message : '로그인 실패'
  } finally { busy.value = false }
}
</script>

<template>
  <div class="login">
    <section class="login-art">
      <div class="row" style="gap:12px">
        <span class="brand-mark"><img src="/logo-mark.png" alt="" /></span>
        <span class="brand-name">SentraAI</span>
      </div>
      <div>
        <h1>코드와 AI 에이전트를<br /><em>한 번에 감사</em>합니다</h1>
        <p>정적 규칙과 LLM 조사, 레드팀 probe를 결합해 취약점을 찾고, 코드 근거와 규제 검토 권고를 보고서로 제공합니다.</p>
        <div class="feat">
          <div><Icon name="code" /><span><b>코드 감사</b>정적 규칙 + ReAct 조사, 근거 라인 검증</span></div>
          <div><Icon name="crosshair" /><span><b>에이전트 레드팀</b>프롬프트 인젝션·권한 상승 probe 와 ASR 측정</span></div>
          <div><Icon name="lock" /><span><b>원문 기반 검토 권고</b>원문 인용·관련성·미확인 조건을 구분해 표시</span></div>
        </div>
      </div>
      <div class="term">$ sentraai <b>✓ 원본 코드 변경 없이 근거와 권장 조치 제공</b></div>
    </section>
    <section class="login-form">
      <div style="position:absolute; top:20px; right:20px"><ThemeToggle /></div>
      <form @submit.prevent="submit">
        <div>
          <h2>로그인</h2>
          <p class="muted" style="margin:6px 0 0">계정으로 대시보드에 접속하세요.</p>
        </div>
        <label class="field">이메일
          <span class="input-icon"><Icon name="mail" :size="16" /><input v-model="email" type="email" autocomplete="username" required /></span>
        </label>
        <label class="field">비밀번호
          <span class="input-icon"><Icon name="lock" :size="16" /><input v-model="password" type="password" autocomplete="current-password" required /></span>
        </label>
        <p v-if="error" class="error" role="alert"><Icon name="alert" :size="16" />{{ error }}</p>
        <button class="btn primary lg" type="submit" :disabled="busy">{{ busy ? '확인 중…' : '로그인' }}</button>
      </form>
    </section>
  </div>
</template>
