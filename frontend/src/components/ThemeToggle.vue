<script setup lang="ts">
import { computed } from 'vue'
import { useTheme, type ThemeMode } from '../composables/theme'
import Icon from './Icon.vue'

const { mode, setMode } = useTheme()
const OPTIONS: { m: ThemeMode; icon: string; label: string }[] = [
  { m: 'light', icon: 'sun', label: '라이트 모드' },
  { m: 'dark', icon: 'moon', label: '다크 모드' },
]
const idx = computed(() => OPTIONS.findIndex((o) => o.m === mode.value))
</script>

<template>
  <div class="theme-toggle" role="radiogroup" aria-label="화면 테마">
    <span class="thumb" :style="{ transform: `translateX(${idx * 34}px)` }" />
    <button v-for="o in OPTIONS" :key="o.m" type="button" role="radio" :data-m="o.m" :class="{ on: mode === o.m }"
      :aria-checked="mode === o.m" :aria-label="o.label" :title="o.label" @click="setMode(o.m)">
      <Icon :name="o.icon" :size="16" />
    </button>
  </div>
</template>
