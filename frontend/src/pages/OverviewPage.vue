<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { get } from '../api/client'
import type { Audit, Paged } from '../api/types'
import Stat from '../components/Stat.vue'
import Icon from '../components/Icon.vue'
import Sev from '../components/Sev.vue'
import Donut from '../components/Donut.vue'
import { useAuth } from '../stores/auth'
import { SEV_COLORS } from '../composables/theme'

const auth = useAuth()
const audits = ref<Audit[]>([])
const error = ref('')
const loaded = ref(false)

onMounted(async () => {
  try {
    audits.value = (await get<Paged<Audit>>('/audits?size=50')).items
  } catch (e) { error.value = (e as Error).message } finally { loaded.value = true }
})
const ORDER = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO']
const sev = computed(() => {
  const t: Record<string, number> = {}
  for (const a of audits.value) for (const [k, v] of Object.entries(a.summary?.by_severity ?? {})) t[k] = (t[k] ?? 0) + v
  return t
})
const sevList = computed(() => ORDER.filter((k) => sev.value[k] != null).map((k) => [k, sev.value[k]] as const))
const total = computed(() => Object.values(sev.value).reduce((a, b) => a + b, 0))
const donut = computed(() => sevList.value.map(([name, value]) => ({ name, value, color: SEV_COLORS[name] })))
const running = computed(() => audits.value.filter((a) => ['QUEUED', 'RUNNING', 'WAITING_APPROVAL', 'WAITING_MERGE'].includes(a.status)).length)
const completed = computed(() => audits.value.filter((a) => a.status === 'COMPLETED').length)
const canRun = computed(() => auth.role === 'ADMIN' || auth.role === 'DEVELOPER')
</script>

<template>
  <p v-if="error" class="error"><Icon name="alert" :size="16" />{{ error }}</p>
  <div class="hero">
    <div class="row" style="justify-content:space-between; align-items:flex-end; gap:20px">
      <div>
        <div class="row small" style="color:#6ee7b7; gap:8px; margin-bottom:8px"><span class="live" /> 실시간 감사 모니터링</div>
        <h2>안녕하세요, {{ auth.user?.name }}님</h2>
        <p>감사 {{ audits.length }}건 중 {{ completed }}건 완료 · 누적 Finding {{ total }}건<template v-if="sev.CRITICAL"> · CRITICAL {{ sev.CRITICAL }}건 확인이 필요합니다</template>.</p>
      </div>
      <div class="row">
        <RouterLink v-if="canRun" to="/audits/new" class="btn primary"><Icon name="play" :size="15" />새 감사 실행</RouterLink>
        <RouterLink to="/eval" class="btn"><Icon name="flask" :size="15" />모델 비교</RouterLink>
      </div>
    </div>
  </div>

  <div class="grid cols-4">
    <Stat label="전체 감사" :value="audits.length" icon="history" color="var(--accent-2)" :hint="`완료 ${completed}건`" />
    <Stat label="진행 중" :value="running" icon="radio" color="var(--accent)" hint="대기·실행 중" />
    <Stat label="CRITICAL" :value="sev.CRITICAL ?? 0" icon="alert" color="var(--critical)" hint="누적 Finding 기준" />
    <Stat label="완료" :value="completed" icon="check-circle" color="var(--accent-2)" hint="분석 및 보고서 생성 완료" />
  </div>

  <div class="grid split mt">
    <div class="card">
      <div class="card-head"><h3><Icon name="layers" :size="16" />최근 감사</h3><span class="spacer" /><RouterLink to="/audits" class="btn ghost sm">전체 보기<Icon name="chevron" :size="14" /></RouterLink></div>
      <div v-if="audits.length" class="table-wrap"><table>
        <thead><tr><th>ID</th><th>상태</th><th>모드</th><th class="r">Finding</th><th>시작</th></tr></thead>
        <tbody><tr v-for="a in audits.slice(0, 8)" :key="a.id">
          <td><RouterLink class="id-link" :to="`/audits/${a.id}`">{{ a.id.slice(0, 8) }}</RouterLink></td>
          <td><span class="status" :class="a.status">{{ a.status }}</span></td>
          <td><span class="badge">{{ a.mode }}</span></td>
          <td class="r num">{{ a.summary?.total ?? '-' }}</td>
          <td class="muted mono">{{ a.created_at?.slice(0, 16).replace('T', ' ') }}</td>
        </tr></tbody>
      </table></div>
      <div v-else-if="loaded" class="empty"><Icon name="inbox" /><b>아직 감사가 없습니다</b><span>감사 실행에서 대상을 골라 시작하세요.</span></div>
    </div>
    <div class="card">
      <div class="card-head"><h3><Icon name="bug" :size="16" />심각도 분포</h3></div>
      <template v-if="sevList.length">
        <Donut :data="donut" label="findings" />
        <div class="chips" style="justify-content:center"><Sev v-for="[k, v] in sevList" :key="k" :v="k" :n="v" /></div>
      </template>
      <div v-else class="empty"><Icon name="check-circle" /><b>완료된 감사가 없습니다</b><span>감사가 끝나면 심각도별 분포가 표시됩니다.</span></div>
    </div>
  </div>
</template>
