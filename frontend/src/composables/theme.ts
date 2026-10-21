// 테마 상태: light | dark | system. index.html 의 인라인 스크립트와 같은 규칙으로 data-theme 을 정한다.
import { computed, ref, watchEffect } from 'vue'

export type ThemeMode = 'light' | 'dark' | 'system'

function readMode(): ThemeMode {
  try {
    const v = localStorage.getItem('theme')
    return v === 'light' || v === 'dark' ? v : 'system'
  } catch { return 'system' }
}

const mq = window.matchMedia('(prefers-color-scheme: dark)')
const mode = ref<ThemeMode>(readMode())
const systemDark = ref(mq.matches)
mq.addEventListener('change', (e) => { systemDark.value = e.matches })

const isDark = computed(() => (mode.value === 'system' ? systemDark.value : mode.value === 'dark'))
watchEffect(() => { document.documentElement.dataset.theme = isDark.value ? 'dark' : 'light' })

function setMode(m: ThemeMode) {
  mode.value = m
  try { localStorage.setItem('theme', m) } catch { /* 저장소 차단 — 이번 세션만 적용 */ }
}

export function useTheme() { return { mode, isDark, setMode } }

// ECharts 는 CSS 변수를 읽지 못하므로 테마별 색을 여기서 넘긴다
export const SEV_COLORS: Record<string, string> = {
  CRITICAL: '#f43f5e', HIGH: '#fb923c', MEDIUM: '#fbbf24', LOW: '#38bdf8', INFO: '#94a3b8',
}
export const CONFIG_COLORS: Record<string, string> = {
  A_nano_single: '#818cf8', B_nano_harness: '#22d39b',
}

export function chartBase() {
  const dark = isDark.value
  const text = dark ? '#b4c0cc' : '#475569'
  const line = dark ? '#1f2935' : '#e3e8ef'
  return {
    text, line,
    base: {
      backgroundColor: 'transparent',
      textStyle: { color: text, fontFamily: 'IBM Plex Sans KR, system-ui, sans-serif' },
      tooltip: {
        backgroundColor: dark ? '#121821' : '#ffffff', borderColor: line,
        textStyle: { color: dark ? '#e6edf3' : '#0f172a' }, extraCssText: 'border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,.18)',
      },
      legend: { textStyle: { color: text }, icon: 'roundRect', itemWidth: 12, itemHeight: 8 },
    },
    axis: {
      axisLine: { lineStyle: { color: line } }, axisTick: { show: false },
      axisLabel: { color: text }, splitLine: { lineStyle: { color: line, type: 'dashed' as const } },
      nameTextStyle: { color: text },
    },
  }
}
