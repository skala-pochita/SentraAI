<script setup lang="ts">
// ISO/IEC 25010 9개 특성 카드 (목표 대비 측정값) — GET /quality
import { onMounted, ref } from 'vue'
import { get } from '../api/client'
interface Card { characteristic: string; design: string; metric: string; target: string; measured: unknown; met: boolean | null; note: string }
const data = ref<{ standard: string; cards: Card[]; eval_run_id: string | null } | null>(null)
onMounted(async () => { data.value = await get('/quality') })
const show = (v: unknown) => (v == null ? '미측정' : typeof v === 'object' ? JSON.stringify(v) : String(v))
</script>
<template>
  <div v-if="data">
    <p class="muted">{{ data.standard }} · 비교 실험 {{ data.eval_run_id?.slice(0, 8) ?? '없음' }}</p>
    <div class="grid cols-3">
      <div v-for="c in data.cards" :key="c.characteristic" class="card">
        <h3 style="margin-top:0">{{ c.characteristic }}
          <span class="badge" :class="c.met === true ? 'sev-LOW' : c.met === false ? 'sev-CRITICAL' : ''">{{ c.met === true ? '충족' : c.met === false ? '미달' : '미측정' }}</span></h3>
        <p class="muted">{{ c.design }}</p>
        <p><b>{{ c.metric }}</b><br />목표 {{ c.target }}</p>
        <pre style="white-space:pre-wrap">{{ show(c.measured) }}</pre>
        <p v-if="c.note" class="muted">{{ c.note }}</p>
      </div>
    </div>
  </div>
</template>
