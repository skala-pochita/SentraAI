import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useAuth, type Role } from '../stores/auth'

declare module 'vue-router' {
  interface RouteMeta { public?: boolean; roles?: Role[]; title?: string; nav?: boolean }
}

const ALL: Role[] = ['ADMIN', 'DEVELOPER', 'AUDITOR']
const ADMIN_DEV: Role[] = ['ADMIN', 'DEVELOPER']
const routes: RouteRecordRaw[] = [
  { path: '/login', component: () => import('../pages/LoginPage.vue'), meta: { public: true } },
  { path: '/', component: () => import('../pages/OverviewPage.vue'), meta: { title: '개요', nav: true, roles: ALL } },
  { path: '/audits/new', component: () => import('../pages/AuditNewPage.vue'), meta: { title: '감사 실행', nav: true, roles: ADMIN_DEV } },
  { path: '/audits', component: () => import('../pages/AuditsPage.vue'), meta: { title: '감사 이력', nav: true, roles: ALL } },
  { path: '/audits/:id', component: () => import('../pages/AuditPage.vue'), meta: { title: '감사 진행', roles: ALL } },
  { path: '/audits/:id/findings', component: () => import('../pages/FindingsPage.vue'), meta: { title: 'Findings', roles: ALL } },
  { path: '/audits/:id/risks', component: () => import('../pages/RisksPage.vue'), meta: { title: '결합 위험', roles: ALL } },
  { path: '/audits/:id/redteam', component: () => import('../pages/RedTeamPage.vue'), meta: { title: '레드팀 결과', roles: ALL } },
  { path: '/audits/:id/report', component: () => import('../pages/ReportPage.vue'), meta: { title: '보고서', roles: ALL } },
  { path: '/findings/:id', component: () => import('../pages/FindingPage.vue'), meta: { title: 'Finding 상세', roles: ALL } },
  { path: '/approvals', component: () => import('../pages/ApprovalsPage.vue'), meta: { title: '승인함', nav: true, roles: ['ADMIN'] } },
  { path: '/quality', component: () => import('../pages/QualityPage.vue'), meta: { title: '품질', nav: true, roles: ALL } },
  { path: '/traces', component: () => import('../pages/TracesPage.vue'), meta: { title: '트레이스', nav: true, roles: ['ADMIN'] } },
  { path: '/knowledge', component: () => import('../pages/KnowledgePage.vue'), meta: { title: '지식 관리', nav: true, roles: ALL } },
  { path: '/ops', component: () => import('../pages/OpsPage.vue'), meta: { title: 'Ops', nav: true, roles: ['ADMIN'] } },
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
