<script setup lang="ts">defineProps<{ label: string; value: string | number; hint?: string }>()</script>
<template>
  <div class="card stat"><div class="muted">{{ label }}</div><div class="big">{{ value }}</div><div v-if="hint" class="muted small">{{ hint }}</div></div>
</template>
<style scoped>.big { font-size: 28px; font-weight: 700; } .small { font-size: 12px; }</style>
