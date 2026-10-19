import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useAuth, type Role } from '../stores/auth'

declare module 'vue-router' {
  interface RouteMeta { public?: boolean; roles?: Role[]; title?: string; nav?: boolean }
}

const ALL: Role[] = ['ADMIN', 'DEVELOPER', 'AUDITOR']
const routes: RouteRecordRaw[] = [
  { path: '/login', component: () => import('../pages/LoginPage.vue'), meta: { public: true } },
  { path: '/', component: () => import('../pages/OverviewPage.vue'), meta: { title: '개요', nav: true, roles: ALL } },
]

const router = createRouter({ history: createWebHistory(), routes })

router.beforeEach(async (to) => {
  const auth = useAuth()
  await auth.load()
  if (to.meta.public) return true
  if (!auth.user) return { path: '/login', query: { next: to.fullPath } }
  if (to.meta.roles && !to.meta.roles.includes(auth.user.role)) return { path: '/' }
  return true
})

export default router
