<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuth } from './stores/auth'

const auth = useAuth()
const route = useRoute()
const router = useRouter()
const navItems = computed(() =>
  router.getRoutes().filter((r) => r.meta.nav && (!r.meta.roles || (auth.role && r.meta.roles.includes(auth.role)))),
)
const dark = computed({
  get: () => document.documentElement.dataset.theme === 'dark',
  set: (v: boolean) => { document.documentElement.dataset.theme = v ? 'dark' : 'light'; localStorage.setItem('theme', v ? 'dark' : 'light') },
})
document.documentElement.dataset.theme = localStorage.getItem('theme') ?? 'light'

async function logout() { await auth.logout(); router.push('/login') }
</script>

<template>
  <div v-if="route.meta.public"><RouterView /></div>
  <div v-else class="layout">
    <aside class="nav">
      <div class="brand">AI Security Auditor</div>
      <RouterLink v-for="r in navItems" :key="r.path" :to="r.path">{{ r.meta.title }}</RouterLink>
    </aside>
    <main>
      <header class="top">
        <span class="title">{{ route.meta.title }}</span>
        <span class="spacer" />
        <label class="theme"><input type="checkbox" v-model="dark" /> 다크</label>
        <span v-if="auth.user" class="user">{{ auth.user.name }} <b class="badge">{{ auth.user.role }}</b></span>
        <button class="btn" @click="logout">로그아웃</button>
      </header>
      <section class="content"><RouterView /></section>
    </main>
  </div>
</template>
