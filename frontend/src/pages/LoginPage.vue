<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuth } from '../stores/auth'
import { ApiError } from '../api/client'

const email = ref('admin@demo.io')
const password = ref('')
const error = ref('')
const auth = useAuth()
const router = useRouter()
const route = useRoute()

async function submit() {
  error.value = ''
  try {
    await auth.login(email.value, password.value)
    router.push((route.query.next as string) || '/')
  } catch (e) {
    error.value = e instanceof ApiError ? e.body.message : '로그인 실패'
  }
}
</script>

<template>
  <div class="login">
    <form class="card" @submit.prevent="submit">
      <h2>AI Security Auditor</h2>
      <label>이메일 <input v-model="email" type="email" required /></label>
      <label>비밀번호 <input v-model="password" type="password" required /></label>
      <p v-if="error" class="error">{{ error }}</p>
      <button class="btn primary" type="submit">로그인</button>
    </form>
  </div>
</template>
