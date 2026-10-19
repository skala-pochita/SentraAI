<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { get, post, put, del, ApiError } from '../api/client'
import { useAuth } from '../stores/auth'
interface Doc { doc_id?: string; id?: string; title: string; source_type: string; ref_code?: string; chunks?: number }
const auth = useAuth()
const docs = ref<Doc[]>([])
const sel = ref<{ id: string; content_md: string } | null>(null)
const form = ref({ title: '', source_type: 'policy', content_md: '' })
const error = ref('')
const idOf = (d: Doc) => (d.doc_id ?? d.id) as string
const load = async () => { docs.value = await get<Doc[]>('/documents') }
onMounted(load)
async function open(d: Doc) { const r = await get<{ content_md: string }>(`/documents/${idOf(d)}`); sel.value = { id: idOf(d), content_md: r.content_md } }
async function save() { if (sel.value) { await put(`/documents/${sel.value.id}`, { content_md: sel.value.content_md }); await load() } }
async function remove(d: Doc) { await del(`/documents/${idOf(d)}`); sel.value = null; await load() }
async function add() { try { await post('/documents', form.value); form.value.title = ''; form.value.content_md = ''; await load() } catch (e) { error.value = e instanceof ApiError ? e.body.message : String(e) } }
</script>
<template>
  <div class="grid cols-2">
    <div class="card"><table><tr><th>제목</th><th>유형</th><th /></tr>
      <tr v-for="d in docs" :key="idOf(d)"><td><a href="#" @click.prevent="open(d)">{{ d.title }}</a></td><td>{{ d.source_type }}</td>
        <td><button v-if="auth.isAdmin" class="btn" @click="remove(d)">삭제</button></td></tr></table>
      <div v-if="auth.isAdmin" class="mt"><h4>문서 추가</h4><input v-model="form.title" placeholder="제목" /><select v-model="form.source_type"><option>policy</option><option>framework</option><option>past_case</option></select>
        <textarea v-model="form.content_md" rows="5" placeholder="Markdown" /><button class="btn primary mt" @click="add">등록</button><p class="error">{{ error }}</p></div></div>
    <div class="card" v-if="sel"><h4>원문</h4><textarea v-model="sel.content_md" rows="22" :readonly="!auth.isAdmin" /><button v-if="auth.isAdmin" class="btn primary mt" @click="save">저장·재인덱싱</button></div>
  </div>
</template>
