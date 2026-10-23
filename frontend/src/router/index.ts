import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useAuth, type Role } from '../stores/auth'
import { get } from '../api/client'

declare module 'vue-router' {
  interface RouteMeta { public?: boolean; roles?: Role[]; title?: string; nav?: boolean; icon?: string; group?: string; desc?: string }
}

const ALL: Role[] = ['ADMIN', 'DEVELOPER', 'AUDITOR']
const ADMIN_DEV: Role[] = ['ADMIN', 'DEVELOPER']
const routes: RouteRecordRaw[] = [
  { path: '/login', component: () => import('../pages/LoginPage.vue'), meta: { public: true } },
  { path: '/', component: () => import('../pages/OverviewPage.vue'), meta: { title: '개요', nav: true, roles: ALL, icon: 'dashboard', group: '대시보드', desc: '보안 상태 한눈에 보기' } },
  { path: '/audits/new', component: () => import('../pages/AuditNewPage.vue'), meta: { title: '감사 실행', nav: true, roles: ADMIN_DEV, icon: 'play', group: '감사', desc: '코드·에이전트 대상 선택 후 감사 시작' } },
  { path: '/audits', component: () => import('../pages/AuditsPage.vue'), meta: { title: '감사 이력', nav: true, roles: ALL, icon: 'history', group: '감사', desc: '지금까지 실행한 감사' } },
  { path: '/audits/:id', component: () => import('../pages/AuditPage.vue'), meta: { title: '감사 진행', roles: ALL, group: '감사', desc: '실시간 감사 진행 상황' } },
  { path: '/audits/:id/findings', component: () => import('../pages/FindingsPage.vue'), meta: { title: 'Findings', roles: ALL, group: '감사', desc: '탐지 결과 — 심각도·신뢰도 순' } },
  { path: '/audits/:id/risks', component: () => import('../pages/RisksPage.vue'), meta: { title: '결합 위험', roles: ALL, group: '감사', desc: '여러 Finding 이 엮여 커지는 위험 경로' } },
  { path: '/audits/:id/redteam', component: () => import('../pages/RedTeamPage.vue'), meta: { title: '레드팀 결과', roles: ALL, group: '감사', desc: 'probe 별 공격 시도와 성공률' } },
  { path: '/audits/:id/compare', component: () => import('../pages/ComparePage.vue'), meta: { title: '수정 전후 비교', roles: ALL, group: '감사', desc: '이전 버전 감사와 비교 · 동일 공격 재검증' } },
  { path: '/audits/:id/report', component: () => import('../pages/ReportPage.vue'), meta: { title: '보고서', roles: ALL, group: '감사', desc: '감사 보고서 (Markdown)' } },
  { path: '/findings/:id', component: () => import('../pages/FindingPage.vue'), meta: { title: 'Finding 상세', roles: ALL, group: '감사', desc: '근거·수정 방향·결합 위험' } },
  { path: '/eval', component: () => import('../pages/CodeEvalPage.vue'), meta: { title: '모델 비교', nav: true, roles: ALL, icon: 'flask', group: '분석', desc: 'A/B 비교 실험 — 작은 모델 + AI Agent 효과' } },
  { path: '/traces', component: () => import('../pages/TracesPage.vue'), meta: { title: '트레이스', nav: true, roles: ['ADMIN'], icon: 'activity', group: '분석', desc: '감사 1건의 node·llm·tool span' } },
  { path: '/knowledge', component: () => import('../pages/KnowledgePage.vue'), meta: { title: '지식 관리', nav: true, roles: ALL, icon: 'book', group: '관리', desc: 'RAG 정책·프레임워크·과거 사례 문서' } },
  { path: '/ops', component: () => import('../pages/OpsPage.vue'), meta: { title: 'Ops', nav: true, roles: ['ADMIN'], icon: 'server', group: '관리', desc: '의존성 상태와 DLQ 재처리' } },
]

const router = createRouter({ history: createWebHistory(), routes })

router.beforeEach(async (to) => {
  const auth = useAuth()
  await auth.load()
  if (auth.local && to.path === '/login') return { path: '/' }
  if (to.meta.public) return true
  if (!auth.user) return { path: '/login', query: { next: to.fullPath } }
  if (to.meta.roles && !to.meta.roles.includes(auth.user.role)) return { path: '/' }
  const auditMatch = to.path.match(/^\/audits\/([^/]+)\/(findings|risks|redteam|report|compare)$/)
  if (auditMatch) {
    const audit = await get<{ status: string }>(`/audits/${auditMatch[1]}`)
    if (audit.status !== 'COMPLETED') return { path: `/audits/${auditMatch[1]}` }
  }
  return true
})

export default router
