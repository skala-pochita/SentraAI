<script setup lang="ts">
import Icon from './Icon.vue'
defineProps<{ label: string; value: string | number; hint?: string; icon?: string; color?: string }>()
</script>
<template>
  <div class="card stat" :style="color ? { '--c': color } : undefined">
    <div class="stat-top"><span class="label">{{ label }}</span><span v-if="icon" class="ico"><Icon :name="icon" :size="17" /></span></div>
    <div class="value">{{ value }}</div>
    <div v-if="hint" class="hint">{{ hint }}</div>
  </div>
</template>
