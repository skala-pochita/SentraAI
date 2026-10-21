<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuth } from './stores/auth'
import Icon from './components/Icon.vue'
import ThemeToggle from './components/ThemeToggle.vue'

const auth = useAuth()
const route = useRoute()
const router = useRouter()
const GROUPS = ['대시보드', '감사', '분석', '관리']
const navItems = computed(() =>
  router.getRoutes().filter((r) => r.meta.nav && (!r.meta.roles || (auth.role && r.meta.roles.includes(auth.role)))),
)
const groups = computed(() =>
  GROUPS.map((g) => ({ g, items: navItems.value.filter((r) => r.meta.group === g) })).filter((x) => x.items.length),
)
const initials = computed(() => (auth.user?.name ?? '?').trim().slice(0, 2).toUpperCase())
const open = ref(false)
watch(() => route.fullPath, () => { open.value = false })

async function logout() { await auth.logout(); router.push('/login') }
</script>

<template>
  <div v-if="route.meta.public"><RouterView /></div>
  <div v-else class="layout">
    <div v-if="open" class="scrim" @click="open = false" />
    <aside class="sidebar" :class="{ open }">
      <RouterLink to="/" class="brand" style="color:inherit">
        <span class="brand-mark"><img src="/logo-mark.png" alt="" /></span>
        <span><div class="brand-name">SentraAI</div><div class="brand-sub">AI Security Auditor</div></span>
      </RouterLink>
      <nav class="nav" aria-label="주 메뉴">
        <div v-for="grp in groups" :key="grp.g" class="nav-group">
          <div class="nav-label">{{ grp.g }}</div>
          <RouterLink v-for="r in grp.items" :key="r.path" :to="r.path">
            <Icon :name="r.meta.icon ?? 'file'" :size="17" />{{ r.meta.title }}
          </RouterLink>
        </div>
      </nav>
      <div class="side-foot">
        <div v-if="auth.user" class="me">
          <span class="avatar">{{ initials }}</span>
          <span style="min-width:0"><div class="me-name">{{ auth.user.name }}</div><div class="me-role">{{ auth.user.role }}</div></span>
          <button class="icon-btn" aria-label="로그아웃" title="로그아웃" @click="logout"><Icon name="logout" :size="17" /></button>
        </div>
      </div>
    </aside>
    <div class="main">
      <header class="topbar">
        <button class="icon-btn menu-btn" aria-label="메뉴 열기" @click="open = true"><Icon name="menu" /></button>
        <div style="min-width:0">
          <div class="crumbs">{{ route.meta.group ?? '' }}<template v-if="route.meta.desc"><Icon name="chevron" :size="12" />{{ route.meta.desc }}</template></div>
          <h1 class="page-title">{{ route.meta.title }}</h1>
        </div>
        <span class="spacer" />
        <ThemeToggle />
      </header>
      <section class="content" :key="route.fullPath"><RouterView /></section>
    </div>
  </div>
</template>
