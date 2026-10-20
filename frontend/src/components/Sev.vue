<script setup lang="ts">defineProps<{ v: string; n?: number }>()</script>
<template><span class="sev" :class="`sev-${v}`">{{ v }}<span v-if="n != null" class="n">{{ n }}</span></span></template>
