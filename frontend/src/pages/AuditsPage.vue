<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { get } from '../api/client'
import type { Audit, Paged, Target } from '../api/types'
import Icon from '../components/Icon.vue'
import Sev from '../components/Sev.vue'
const rows = ref<Audit[]>([])
const targets = ref<Target[]>([])
const loaded = ref(false)
const error = ref('')
const filter = ref('')
const query = ref('')
const expanded = ref<string[]>([])
const picked = ref<string[]>([])
const statusLabel: Record<string, string> = { COMPLETED: '검사 완료', FAILED: '검사 실패', RUNNING: '검사 중', QUEUED: '대기 중', WAITING_APPROVAL: '승인 대기', CANCELLED: '취소됨' }
const modeLabel: Record<string, string> = { full: '전체 보안 검사', code: '코드 검사', agent: '모의 공격 검사' }
onMounted(async () => {
  try {
    const [audits, projects] = await Promise.all([get<Paged<Audit>>('/audits?size=100'), get<Paged<Target>>('/targets?size=200')])
    rows.value = audits.items; targets.value = projects.items
  } catch { error.value = '감사 이력을 불러오지 못했습니다. 새로고침해 주세요.' }
  finally { loaded.value = true }
})
const targetOf = (a: Audit) => targets.value.find(t => a.target_ids?.includes(t.id) && t.type === 'repo') ?? targets.value.find(t => a.target_ids?.includes(t.id))
const nameOf = (a: Audit) => a.name || targetOf(a)?.name || '이전 감사'
const groupKey = (a: Audit) => targetOf(a)?.config?.project_id || targetOf(a)?.id || a.id
const statuses = computed(() => [...new Set(rows.value.map(a => a.status))])
const groups = computed(() => {
  const map = new Map<string, { key: string; name: string; audits: Audit[] }>()
  for (const a of rows.value) {
    if ((filter.value && a.status !== filter.value) || (query.value && !nameOf(a).toLowerCase().includes(query.value.toLowerCase()) && !targetOf(a)?.name.toLowerCase().includes(query.value.toLowerCase()))) continue
    const key = groupKey(a)
    if (!map.has(key)) map.set(key, { key, name: targetOf(a)?.name || nameOf(a), audits: [] })
    map.get(key)!.audits.push(a)
  }
  return [...map.values()]
})
const selectedAudits = computed(() => rows.value.filter(a => picked.value.includes(a.id)))
const ORDER = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO']
const sevs = (a: Audit) => ORDER.filter(k => a.summary?.by_severity?.[k]).map(k => [k, a.summary!.by_severity![k]] as const)
function toggle(key: string) { expanded.value = expanded.value.includes(key) ? expanded.value.filter(k => k !== key) : [...expanded.value, key] }
</script>
<template>
  <div class="row mb" style="justify-content:space-between; flex-wrap:wrap; gap:16px"><div><h2>프로젝트별 감사 기록</h2><p class="muted">프로젝트를 펼쳐 버전별 결과를 확인하고, 함께 살펴볼 검사를 선택하세요.</p></div><RouterLink class="btn primary" to="/audits/new"><Icon name="play" :size="16" />새 보안 검사 시작</RouterLink></div>
  <input v-model="query" class="mb" placeholder="프로젝트 또는 감사 이름으로 검색" aria-label="감사 이름 검색" />
  <div class="chips mb">
    <button class="chip-btn" :class="{ on: !filter }" @click="filter = ''">모든 검사 {{ rows.length }}</button>
    <button v-for="s in statuses" :key="s" class="chip-btn" :class="{ on: filter === s }" @click="filter = s">{{ statusLabel[s] || s }}</button>
  </div>
  <div v-if="selectedAudits.length" class="card mb selection-panel">
    <div class="row mb"><b>선택한 검사 결과 {{ selectedAudits.length }}개</b><button class="btn ghost sm" @click="picked = []">선택 모두 해제</button></div>
    <div class="result-comparison"><article v-for="a in selectedAudits" :key="a.id"><b>{{ nameOf(a) }}</b><p class="muted small">v{{ targetOf(a)?.config?.version ?? 1 }} · {{ modeLabel[a.mode] }}</p><p>발견된 보안 문제 <strong>{{ a.summary?.total ?? '—' }}</strong>건</p><div class="chips mb"><Sev v-for="[k, v] in sevs(a)" :key="k" :v="k" :n="v" /></div><RouterLink class="btn sm" :to="`/audits/${a.id}`">{{ a.status === 'COMPLETED' ? '검사 결과 보기' : '진행 상황 보기' }}</RouterLink></article></div>
    <p class="muted small mt">선택한 결과의 요약을 나란히 표시합니다. 상세 변경 내역은 결과 화면의 ‘수정 전후 비교’에서 확인하세요.</p>
  </div>
  <p v-if="error" class="error" role="alert">{{ error }}</p>
  <div v-for="g in groups" :key="g.key" class="card flush mb">
    <button class="project-heading" :aria-expanded="expanded.includes(g.key)" @click="toggle(g.key)"><span class="row"><Icon name="layers" :size="20" /><b>{{ g.name }}</b><span class="badge">검사 {{ g.audits.length }}건</span></span><span class="muted small">{{ expanded.includes(g.key) ? '버전별 기록 접기' : '버전별 기록 보기' }} ▾</span></button>
    <div v-if="expanded.includes(g.key)" class="table-wrap"><table>
      <thead><tr><th>선택</th><th>감사 이름 / 버전</th><th>상태</th><th>검사 방식</th><th>보안 문제</th><th>검사 일시</th><th /></tr></thead>
      <tbody><tr v-for="a in g.audits" :key="a.id">
        <td><input v-model="picked" type="checkbox" :value="a.id" :aria-label="`${nameOf(a)} 검사 결과 선택`" /></td>
        <td><RouterLink :to="`/audits/${a.id}`"><b>{{ nameOf(a) }}</b></RouterLink><div class="muted small">버전 {{ targetOf(a)?.config?.version ?? 1 }}</div></td>
        <td><span class="status" :class="a.status">{{ statusLabel[a.status] || a.status }}</span></td>
        <td>{{ modeLabel[a.mode] || a.mode }}</td>
        <td class="num">{{ a.summary?.total ?? '—' }}<span class="muted small"> 건</span></td>
        <td class="muted small">{{ a.created_at?.slice(0, 16).replace('T', ' ') }}</td>
        <td><RouterLink :to="`/audits/${a.id}`" class="btn ghost sm">{{ a.status === 'COMPLETED' ? '결과 보기' : '진행 보기' }}<Icon name="chevron" :size="15" /></RouterLink></td>
      </tr></tbody>
    </table></div>
  </div>
  <div v-if="loaded && !groups.length && !error" class="card empty"><Icon name="inbox" /><b>조건에 맞는 감사 기록이 없습니다</b><RouterLink to="/audits/new" class="btn">프로젝트 보안 검사 시작</RouterLink></div>
</template>
<style scoped>
.project-heading { width:100%; border:0; background:transparent; color:var(--text); padding:22px; display:flex; align-items:center; justify-content:space-between; gap:16px; cursor:pointer; text-align:left; flex-wrap:wrap; }
.selection-panel { border-color:var(--accent); }
.result-comparison { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:14px; }
.result-comparison article { background:var(--surface-2); padding:18px; border-radius:12px; }
input[type=checkbox] { width:18px; height:18px; accent-color:var(--accent); }
</style>
