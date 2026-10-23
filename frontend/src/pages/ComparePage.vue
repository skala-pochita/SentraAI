<script setup lang="ts">
// 수정본 재업로드 비교: 기준 감사(이전 버전) 대비 Finding 변화와 동일 공격 재검증 결과
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { get, ApiError } from '../api/client'
import type { Audit, BaselineCandidate, Comparison, CompareStatus } from '../api/types'
import Icon from '../components/Icon.vue'
import Sev from '../components/Sev.vue'
import Stat from '../components/Stat.vue'
import AuditTabs from '../components/AuditTabs.vue'

const id = useRoute().params.id as string
const candidates = ref<BaselineCandidate[]>([])
const baseline = ref('')
const result = ref<Comparison | null>(null)
const error = ref('')
const loading = ref(false)
const filter = ref<CompareStatus | ''>('')

const TONE: Record<CompareStatus, string> = { NEW: 'bad', PERSISTING: 'warn', RESOLVED: 'ok', BLOCKED: 'ok', UNVERIFIED: 'info', SAFE: '' }
const ICON: Record<CompareStatus, string> = { NEW: 'alert', PERSISTING: 'alert', RESOLVED: 'check', BLOCKED: 'shield', UNVERIFIED: 'clock', SAFE: 'shield' }
const FINDING_STATUSES: CompareStatus[] = ['NEW', 'PERSISTING', 'RESOLVED', 'UNVERIFIED']
const LABEL: Record<string, string> = { NEW: '신규 발견', PERSISTING: '여전히 존재', RESOLVED: '해소·차단', UNVERIFIED: '확인 불가' }
const BENIGN: Record<string, string> = { PASS: '통과', FAIL: '실패', NOT_RUN: '미실행' }

onMounted(async () => {
  const audit = await get<Audit>(`/audits/${id}`)
  baseline.value = audit.baseline_audit_id ?? ''
  const tid = audit.target_ids?.[0]
  if (tid) candidates.value = (await get<BaselineCandidate[]>(`/targets/${tid}/baselines`)).filter((c) => c.id !== id)
})
watch(baseline, async (b) => {
  result.value = null; error.value = ''
  if (!b) return
  loading.value = true
  try { result.value = await get<Comparison>(`/audits/${id}/compare?baseline=${b}`) }
  catch (e) { error.value = e instanceof ApiError ? e.body.message : String(e) }
  finally { loading.value = false }
})
const shown = computed(() => (result.value?.findings ?? []).filter((f) => !filter.value || f.status === filter.value))
const v = (n: number | null | undefined) => (n ? `v${n}` : '버전 미지정')
const day = (s: string) => s.slice(0, 16).replace('T', ' ')
async function download() {
  const md = await get<string>(`/audits/${id}/compare.md?baseline=${baseline.value}`)
  const url = URL.createObjectURL(new Blob([md], { type: 'text/markdown' }))
  const a = document.createElement('a')
  a.href = url; a.download = `compare_${id.slice(0, 8)}.md`; a.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <AuditTabs :id="id" />
  <div class="card mb">
    <div class="row" style="justify-content:space-between">
      <label class="field" style="min-width:320px; flex:1">비교 기준 감사 (같은 프로젝트의 이전 감사)
        <select v-model="baseline" aria-label="비교 기준 감사">
          <option value="">기준 감사를 선택하세요</option>
          <option v-for="c in candidates" :key="c.id" :value="c.id">{{ v(c.version) }} · {{ day(c.created_at) }} · {{ c.mode }} · Finding {{ c.summary?.total ?? 0 }}건 · {{ c.id.slice(0, 8) }}</option>
        </select>
      </label>
      <button class="btn sm" :disabled="!result" @click="download"><Icon name="download" :size="14" />.md 다운로드</button>
    </div>
    <p v-if="!candidates.length" class="muted small mt-s">비교할 이전 감사가 없습니다. 수정본 ZIP을 올릴 때 이전 버전을 연결한 뒤 감사를 실행하세요.</p>
    <p class="muted small mt-s">확인 불가는 해결로 세지 않습니다. 감사기는 원본을 수정하지 않으며, 사용자가 고친 수정본을 같은 공격으로 다시 검사한 결과입니다.</p>
  </div>

  <p v-if="error" class="error"><Icon name="alert" :size="16" />{{ error }}</p>
  <p v-if="loading" class="muted">비교 중…</p>

  <template v-if="result">
    <div class="row mb muted small">
      <span class="badge">기준 {{ v(result.baseline.version) }} · {{ result.baseline.model_verification }}</span>
      <Icon name="chevron" :size="14" />
      <span class="badge">이번 {{ v(result.current.version) }} · {{ result.current.model_verification }}</span>
      <span>정상 대조군: 기준 {{ BENIGN[result.benign.baseline] }} · 이번 {{ BENIGN[result.benign.current] }}</span>
    </div>
    <div v-for="n in result.notes" :key="n" class="callout mb"><Icon name="alert" :size="16" /><span>{{ n }}</span></div>

    <div class="grid cols-4 mb">
      <Stat label="신규 발견" :value="result.summary.NEW" icon="alert" color="var(--critical)" />
      <Stat label="여전히 존재" :value="result.summary.PERSISTING" icon="clock" color="var(--warn)" />
      <Stat label="해소·차단" :value="result.summary.RESOLVED" icon="check" color="var(--ok)" />
      <Stat label="확인 불가" :value="result.summary.UNVERIFIED" icon="search" color="var(--info)" hint="해결로 세지 않음" />
    </div>

    <div v-if="result.probes.length" class="card flush mb">
      <div class="card-head"><h3><Icon name="crosshair" :size="16" />동일 공격 재검증</h3><span class="sub">같은 probe 를 수정본에 다시 실행</span></div>
      <div class="table-wrap"><table>
        <thead><tr><th>Probe</th><th>카테고리</th><th class="r">기준 (성공/시도)</th><th class="r">이번 (성공/시도)</th><th>판정</th><th>사유</th></tr></thead>
        <tbody><tr v-for="p in result.probes" :key="p.probe_id">
          <td class="mono strong" style="color:var(--text)">{{ p.probe_id }}</td>
          <td><span class="badge mono">{{ p.category }}</span></td>
          <td class="r num">{{ p.baseline ? `${p.baseline.successes}/${p.baseline.attempts}` : '-' }}</td>
          <td class="r num">{{ p.current ? `${p.current.successes}/${p.current.attempts}` : '-' }}</td>
          <td><span class="badge tone" :class="TONE[p.status]"><Icon :name="ICON[p.status]" :size="12" />{{ p.label }}</span></td>
          <td class="muted small">{{ p.reason }}</td>
        </tr></tbody>
      </table></div>
    </div>

    <div class="row mb">
      <div class="chips" role="group" aria-label="판정 필터">
        <button class="chip-btn" :class="{ on: !filter }" @click="filter = ''">전체 <span class="n">{{ result.findings.length }}</span></button>
        <button v-for="s in FINDING_STATUSES" :key="s" class="chip-btn" :class="{ on: filter === s }" :disabled="!result.summary[s as keyof Comparison['summary']]" @click="filter = s">
          {{ LABEL[s] }} <span class="n">{{ result.summary[s as keyof Comparison['summary']] }}</span>
        </button>
      </div>
    </div>
    <div class="card flush">
      <div v-if="shown.length" class="table-wrap"><table>
        <thead><tr><th>판정</th><th>심각도</th><th>제목</th><th>위치</th><th>사유</th></tr></thead>
        <tbody><tr v-for="(f, i) in shown" :key="i">
          <td><span class="badge tone" :class="TONE[f.status]"><Icon :name="ICON[f.status]" :size="12" />{{ f.label }}</span></td>
          <td><Sev :v="f.severity" /></td>
          <td>
            <RouterLink v-if="f.current_finding_id" :to="`/findings/${f.current_finding_id}`">{{ f.title }}</RouterLink>
            <RouterLink v-else-if="f.baseline_finding_id" :to="`/findings/${f.baseline_finding_id}`">{{ f.title }}</RouterLink>
            <span v-else>{{ f.title }}</span>
          </td>
          <td class="mono small">{{ f.location }}</td>
          <td class="muted small">{{ f.reason }}</td>
        </tr></tbody>
      </table></div>
      <div v-else class="empty"><Icon name="inbox" /><b>표시할 Finding 이 없습니다</b></div>
    </div>
  </template>
</template>
