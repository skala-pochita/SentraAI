<script setup lang="ts">defineProps<{ v: string }>()</script>
<template><span class="badge" :class="`sev-${v}`">{{ v }}</span></template>
