<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { get, post, put, del, ApiError } from '../api/client'
import { useAuth } from '../stores/auth'
import Icon from '../components/Icon.vue'
interface Doc { doc_id?: string; id?: string; title: string; source_type: string; ref_code?: string; chunks?: number }
const auth = useAuth()
const docs = ref<Doc[]>([])
const sel = ref<{ id: string; title: string; content_md: string } | null>(null)
const form = ref({ title: '', source_type: 'policy', content_md: '' })
const error = ref('')
const saved = ref(false)
const idOf = (d: Doc) => (d.doc_id ?? d.id) as string
const load = async () => { docs.value = await get<Doc[]>('/knowledge/documents') }
onMounted(load)
async function open(d: Doc) { const r = await get<{ content_md: string }>(`/knowledge/documents/${idOf(d)}`); sel.value = { id: idOf(d), title: d.title, content_md: r.content_md }; saved.value = false }
async function save() { if (sel.value) { await put(`/knowledge/documents/${sel.value.id}`, { content_md: sel.value.content_md }); await load(); saved.value = true } }
async function remove(d: Doc) { await del(`/knowledge/documents/${idOf(d)}`); sel.value = null; await load() }
async function add() { try { await post('/knowledge/documents', form.value); form.value.title = ''; form.value.content_md = ''; error.value = ''; await load() } catch (e) { error.value = e instanceof ApiError ? e.body.message : String(e) } }
// 백엔드 KB_SOURCE_TYPES 와 같은 목록
const TYPES = ['policy', 'framework', 'past_case', 'cwe', 'owasp_top10', 'owasp_llm', 'owasp_agentic']
const TYPE_TONE: Record<string, string> = { policy: 'ok', framework: 'info', past_case: 'violet', cwe: 'warn', owasp_top10: 'bad', owasp_llm: 'bad', owasp_agentic: 'bad' }
</script>
<template>
  <div class="grid side">
    <div class="stack" style="gap:16px">
      <div class="card" style="padding:12px">
        <p class="section-title" style="padding:6px 8px 0">문서 {{ docs.length }}개</p>
        <div v-for="d in docs" :key="idOf(d)" class="doc" :class="{ on: sel?.id === idOf(d) }">
          <button class="doc-main" @click="open(d)">
            <Icon name="file" :size="16" />
            <span style="min-width:0; flex:1"><span class="doc-title">{{ d.title }}</span>
              <span class="row small" style="gap:6px; margin-top:4px"><span class="badge tone" :class="TYPE_TONE[d.source_type] ?? ''">{{ d.source_type }}</span><span v-if="d.ref_code" class="muted mono">{{ d.ref_code }}</span><span v-if="d.chunks" class="muted">· {{ d.chunks }} chunks</span></span></span>
          </button>
          <button v-if="auth.isAdmin" class="icon-btn del" :aria-label="`${d.title} 삭제`" title="삭제" @click="remove(d)"><Icon name="trash" :size="15" /></button>
        </div>
        <div v-if="!docs.length" class="empty"><Icon name="book" /><b>문서가 없습니다</b></div>
      </div>
      <div v-if="auth.isAdmin" class="card">
        <div class="card-head"><h3><Icon name="plus" :size="16" />문서 추가</h3></div>
        <div class="stack">
          <label class="field">제목<input v-model="form.title" placeholder="예: 비밀값 관리 정책" /></label>
          <label class="field">유형<select v-model="form.source_type"><option v-for="t in TYPES" :key="t">{{ t }}</option></select></label>
          <label class="field">본문 (Markdown)<textarea v-model="form.content_md" rows="5" placeholder="# 제목" /></label>
          <p v-if="error" class="error"><Icon name="alert" :size="16" />{{ error }}</p>
          <button class="btn primary" @click="add"><Icon name="plus" :size="15" />등록</button>
        </div>
      </div>
    </div>
    <div class="card" v-if="sel">
      <div class="card-head"><h3><Icon name="book" :size="16" />{{ sel.title }}</h3><span class="sub">원문</span><span class="spacer" />
        <span v-if="saved" class="badge tone ok"><Icon name="check" />재인덱싱 완료</span>
        <button v-if="auth.isAdmin" class="btn primary sm" @click="save"><Icon name="save" :size="14" />저장·재인덱싱</button></div>
      <textarea v-model="sel.content_md" rows="26" :readonly="!auth.isAdmin" @input="saved = false" />
    </div>
    <div v-else class="card empty"><Icon name="book" /><b>문서를 선택하세요</b><span>RAG 검색에 쓰이는 정책·프레임워크·과거 사례 원문을 보고 고칠 수 있습니다.</span></div>
  </div>
</template>
<style scoped>
.doc { display: flex; align-items: center; gap: 4px; border-radius: 10px; transition: background .15s; }
.doc:hover { background: var(--surface-2); }
.doc.on { background: var(--accent-soft); }
.doc.on .doc-main > .icon { color: var(--accent); }
.doc-main { flex: 1; min-width: 0; display: flex; gap: 10px; align-items: flex-start; padding: 10px; border: 0; background: none; color: inherit; text-align: left; cursor: pointer; font: inherit; border-radius: 10px; }
.doc-main > .icon { margin-top: 2px; color: var(--muted); }
.doc-title { display: block; color: var(--text); font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.del { color: var(--muted); opacity: 0; }
.doc:hover .del, .del:focus-visible { opacity: 1; }
.del:hover { color: var(--critical); background: color-mix(in srgb, var(--critical) 10%, transparent); }
.spacer { flex: 1; }
</style>
