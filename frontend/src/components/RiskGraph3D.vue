<script setup lang="ts">
// 근거가 있는 단계·관계를 표시한다. 구성 Finding 배열에 실행 순서 의미를 부여하지 않는다.
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as THREE from 'three'
import type { RiskPath } from '../api/types'
import { createStage, cssColor, reducedMotion, SEV_VAR, WebGLUnavailable, type Stage } from '../three/stage'
import { useTheme } from '../composables/theme'

const props = defineProps<{ paths: RiskPath[]; focus?: string | null }>()
const emit = defineEmits<{ inspect: [value: { pathId: string; stepId: string }] }>()
const { isDark } = useTheme()
const host = ref<HTMLElement | null>(null)
const error = ref('')
const tip = ref<{ x: number; y: number; id: string } | null>(null)


const SEV_SIZE: Record<string, number> = { CRITICAL: 0.95, HIGH: 0.8, MEDIUM: 0.68, LOW: 0.58, INFO: 0.5 }


interface GNode { title: string; pathId: string; stepId: string; id: string; sev: string; source: string; pos: THREE.Vector3; mesh?: THREE.Mesh; paths: Set<string> }
interface GEdge { conditional: boolean; a: GNode; b: GNode; path: RiskPath; curve?: THREE.QuadraticBezierCurve3; line?: THREE.Line }

let stage: Stage | null = null
let nodes: GNode[] = []
let edges: GEdge[] = []
const ray = new THREE.Raycaster()
const pointer = new THREE.Vector2()
let hovered: GNode | null = null
let downAt = { x: 0, y: 0 }

function geometryFor(source: string, s: number): THREE.BufferGeometry {
  if (source === 'code') return new THREE.BoxGeometry(s * 1.15, s * 1.15, s * 1.15)
  if (source === 'agent') return new THREE.OctahedronGeometry(s * 0.95)
  if (source === 'correlation') return new THREE.IcosahedronGeometry(s * 0.9)
  return new THREE.SphereGeometry(s * 0.7, 24, 16)
}

function buildGraph() {
  const byId = new Map<string, GNode>()
  edges = []
  for (const p of props.paths) {
    if (!p.payload) continue
    for (const step of p.payload.steps) {
      const id = `${p.id}:${step.id}`
      const n: GNode = { id, title: step.title, pathId: p.id, stepId: step.id, sev: p.severity,
        source: step.kind === 'code' ? 'code' : step.kind === 'impact' ? 'correlation' : 'agent', pos: new THREE.Vector3(), paths: new Set([p.id]) }
      byId.set(id, n)
    }
    for (const edge of p.payload.edges) {
      const a = byId.get(`${p.id}:${edge.from_step_id}`)
      const b = byId.get(`${p.id}:${edge.to_step_id}`)
      if (a && b) edges.push({ a, b, path: p, conditional: edge.evidence_level === 'CONDITIONAL' })
    }
  }
  nodes = [...byId.values()]
  layout()
}

// 노드가 수십 개 수준이라 단순 3D 힘 배치를 미리 돌려 위치를 고정한다
function layout() {
  const n = nodes.length
  const golden = Math.PI * (3 - Math.sqrt(5))
  nodes.forEach((v, i) => {
    const y = n > 1 ? 1 - (i / (n - 1)) * 2 : 0
    const r = Math.sqrt(1 - y * y)
    v.pos.set(Math.cos(golden * i) * r, y, Math.sin(golden * i) * r).multiplyScalar(4)
  })
  const force = nodes.map(() => new THREE.Vector3())
  const d = new THREE.Vector3()
  for (let it = 0; it < 400; it++) {
    force.forEach((f) => f.set(0, 0, 0))
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        d.subVectors(nodes[i]!.pos, nodes[j]!.pos)
        const len2 = Math.max(d.lengthSq(), 0.01)
        d.normalize().multiplyScalar(8 / len2)
        force[i]!.add(d); force[j]!.sub(d)
      }
    }
    for (const e of edges) {
      d.subVectors(e.b.pos, e.a.pos)
      const len = d.length() || 0.01
      d.multiplyScalar(((len - 3.2) / len) * 0.08)
      force[nodes.indexOf(e.a)]!.add(d); force[nodes.indexOf(e.b)]!.sub(d)
    }
    const cool = 1 - it / 400
    nodes.forEach((v, i) => {
      force[i]!.addScaledVector(v.pos, -0.015)
      v.pos.addScaledVector(force[i]!.clampLength(0, 1), cool)
    })
  }
  const center = new THREE.Vector3()
  nodes.forEach((v) => center.add(v.pos)); if (n) center.divideScalar(n)
  const radius = Math.max(1, ...nodes.map((v) => v.pos.distanceTo(center)))
  nodes.forEach((v) => v.pos.sub(center).multiplyScalar(Math.min(1.4, 6 / radius)))
}

function populate(s: Stage) {
  s.scene.add(new THREE.AmbientLight(0xffffff, 1.6))
  const key = new THREE.DirectionalLight(0xffffff, 2.2); key.position.set(5, 8, 6); s.scene.add(key)
  const rim = new THREE.DirectionalLight(0xffffff, 0.8); rim.position.set(-6, -3, -5); s.scene.add(rim)

  for (const v of nodes) {
    const mat = new THREE.MeshStandardMaterial({ roughness: 0.35, metalness: 0.15, transparent: true, flatShading: v.source !== 'unknown' })
    v.mesh = new THREE.Mesh(geometryFor(v.source, SEV_SIZE[v.sev] ?? 0.6), mat)
    v.mesh.position.copy(v.pos)
    v.mesh.rotation.set(Math.random() * 3, Math.random() * 3, 0)
    v.mesh.userData.node = v
    s.scene.add(v.mesh)
  }
  // 같은 두 노드를 잇는 경로가 여럿이면 곡률을 달리해 겹치지 않게 한다
  const seen = new Map<string, number>()
  for (const e of edges) {
    const k = [e.a.id, e.b.id].sort().join('|')
    const nth = seen.get(k) ?? 0; seen.set(k, nth + 1)
    const mid = e.a.pos.clone().add(e.b.pos).multiplyScalar(0.5)
    const side = new THREE.Vector3().subVectors(e.b.pos, e.a.pos).cross(new THREE.Vector3(0, 1, 0))
    if (side.lengthSq() < 1e-4) side.set(1, 0, 0)
    side.normalize().multiplyScalar(0.6 + nth * 0.9 * (nth % 2 ? -1 : 1))
    e.curve = new THREE.QuadraticBezierCurve3(e.a.pos, mid.add(side), e.b.pos)
    e.line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(e.curve.getPoints(32)), e.conditional ? new THREE.LineDashedMaterial({ transparent: true, dashSize: 0.15, gapSize: 0.12 }) : new THREE.LineBasicMaterial({ transparent: true }))
    e.line.computeLineDistances()
    s.scene.add(e.line)

  }
  applyColors()

  s.onFrame((dt) => {
    for (const v of nodes) {
      if (!v.mesh) continue
      if (!reducedMotion) { v.mesh.rotation.x += dt * 0.25; v.mesh.rotation.y += dt * 0.35 }
      const target = v === hovered ? 1.3 : 1
      v.mesh.scale.lerp(new THREE.Vector3(target, target, target), 0.2)
    }

  })
}

// 테마·focus 가 바뀌면 색과 투명도만 다시 칠한다
function applyColors() {
  const dim = (pathIds: Set<string> | string) => {
    if (!props.focus) return false
    return typeof pathIds === 'string' ? pathIds !== props.focus : !pathIds.has(props.focus)
  }
  for (const v of nodes) {
    const m = v.mesh?.material as THREE.MeshStandardMaterial | undefined
    if (!m) continue
    const c = cssColor(SEV_VAR[v.sev] ?? '--info')
    m.color.copy(c); m.emissive.copy(c).multiplyScalar(isDark.value ? 0.45 : 0.15)
    m.opacity = dim(v.paths) ? 0.15 : 1
  }
  for (const e of edges) {
    const c = cssColor(SEV_VAR[e.path.severity] ?? '--info')
    const lm = e.line?.material as THREE.LineBasicMaterial | undefined
    lm?.color.copy(c)
    if (lm) lm.opacity = dim(e.path.id) ? 0.06 : 0.7

  }
}

function setPointer(ev: PointerEvent) {
  const r = (ev.currentTarget as HTMLElement).getBoundingClientRect()
  pointer.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1)
  return r
}
function pick(): GNode | null {
  if (!stage) return null
  ray.setFromCamera(pointer, stage.camera)
  const hit = ray.intersectObjects(nodes.map((v) => v.mesh!).filter(Boolean))[0]
  return (hit?.object.userData.node as GNode | undefined) ?? null
}
function onMove(ev: PointerEvent) {
  const r = setPointer(ev)
  hovered = pick()
  if (stage) stage.renderer.domElement.style.cursor = hovered ? 'pointer' : 'grab'
  tip.value = hovered ? { x: ev.clientX - r.left, y: ev.clientY - r.top, id: hovered.title } : null
}
function onLeave() { hovered = null; tip.value = null }
function onDown(ev: PointerEvent) { downAt = { x: ev.clientX, y: ev.clientY } }
// 드래그(회전)와 클릭을 구분해 클릭일 때만 상세로 이동한다
function onUp(ev: PointerEvent) {
  if (Math.hypot(ev.clientX - downAt.x, ev.clientY - downAt.y) > 5) return
  setPointer(ev)
  const n = pick()
  if (n) emit('inspect', { pathId: n.pathId, stepId: n.stepId })
}

function mount() {
  stage?.dispose(); stage = null
  if (!host.value || !props.paths.length) return
  try {
    stage = createStage(host.value, { distance: 24, height: 8, autoRotate: 0.6 })
  } catch (e) {
    if (e instanceof WebGLUnavailable) { error.value = '이 브라우저에서 WebGL 을 사용할 수 없어 3D 그래프를 표시하지 못했습니다.'; return }
    throw e
  }
  buildGraph(); populate(stage)
}

onMounted(mount)
onBeforeUnmount(() => stage?.dispose())
watch(() => props.paths, mount)
watch(() => props.focus, applyColors)
watch(isDark, () => void nextTick(applyColors))
</script>

<template>
  <div class="graph">
    <div ref="host" class="canvas" @pointermove="onMove" @pointerleave="onLeave" @pointerdown="onDown" @pointerup="onUp" />
    <p v-if="error" class="muted small fallback">{{ error }}</p>
    <div class="legend small">
      <span><i class="shape box" />코드</span><span><i class="shape oct" />입력·호출·통제</span><span><i class="shape ico" />영향</span>
      <span class="muted">· 점선 = 조건부 연결 · 선은 실제 시간 순서를 뜻하지 않습니다</span>
    </div>
    <p class="hint muted small">드래그 회전 · 휠 확대 · 노드 클릭 → 상세</p>
    <div v-if="tip" class="tip" :style="{ left: `${tip.x + 14}px`, top: `${tip.y + 14}px` }">
      <b>{{ tip.id }}</b>
    </div>
  </div>
</template>

<style scoped>
.graph { position: relative; height: 420px; border-radius: var(--radius); overflow: hidden;
  background: radial-gradient(ellipse at 50% 40%, var(--surface-2), var(--surface) 70%); border: 1px solid var(--border); }
.canvas { position: absolute; inset: 0; cursor: grab; touch-action: none; }
.canvas:active { cursor: grabbing; }
.fallback { position: absolute; inset: 0; display: grid; place-items: center; padding: 24px; text-align: center; }
.legend { position: absolute; left: 12px; top: 10px; display: flex; gap: 12px; align-items: center; color: var(--text-2); pointer-events: none; flex-wrap: wrap; }
.legend span { display: inline-flex; align-items: center; gap: 5px; }
.shape { display: inline-block; width: 9px; height: 9px; background: var(--text-2); }
.shape.oct { transform: rotate(45deg) scale(.85); }
.shape.ico { clip-path: polygon(50% 0, 100% 25%, 100% 75%, 50% 100%, 0 75%, 0 25%); }
.hint { position: absolute; right: 12px; bottom: 8px; pointer-events: none; }
.tip { position: absolute; z-index: 2; max-width: 280px; display: flex; flex-direction: column; gap: 4px; padding: 10px 12px; pointer-events: none;
  background: var(--surface); border: 1px solid var(--border); border-radius: 10px; box-shadow: var(--shadow-lg); }
.tip .sev { align-self: flex-start; }
.tip b { color: var(--text); font-size: 13px; }
</style>
