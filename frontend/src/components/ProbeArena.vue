<script setup lang="ts">
// 레드팀 공격 시각화: 가운데 구체가 감사 대상 에이전트, 둘레의 발사대가 probe 다.
// 차단된 시도는 방어막에 튕기고, 성공한 시도는 방어막을 뚫고 코어를 붉게 물들인다.
// feed 에 쌓인 시도를 순서대로 발사하므로 실시간(SSE)·재생 양쪽에서 같은 컴포넌트를 쓴다.
import { nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import * as THREE from 'three'
import { createStage, cssColor, reducedMotion, WebGLUnavailable, type Shot, type Stage } from '../three/stage'
import { useTheme } from '../composables/theme'

const props = withDefaults(defineProps<{ feed: Shot[]; interval?: number; height?: number }>(), { interval: 160, height: 360 })
const { isDark } = useTheme()
const host = ref<HTMLElement | null>(null)
const error = ref('')
const stats = reactive({ fired: 0, blocked: 0, breached: 0 })
const labels = ref<{ id: string; x: number; y: number; show: boolean; breached: boolean }[]>([])

const RING = 7.5
const SHIELD = 2.6
const CORE = 1.1
const FLIGHT = 0.85

interface Lane { id: string; angle: number; target: number; y: number; mesh: THREE.Mesh; breached: boolean }
interface Fx { update(dt: number): boolean; dispose(): void }

let stage: Stage | null = null
let lanes: Lane[] = []
let fx: Fx[] = []
let consumed = 0
let queue: Shot[] = []
let cooldown = 0
let shieldFlash = 0
let coreFlash = 0
let core: THREE.Mesh, coreWire: THREE.LineSegments, shield: THREE.LineSegments, shieldFill: THREE.Mesh, orbit: THREE.Line
const shotGeo = new THREE.SphereGeometry(0.13, 12, 10)
const ringGeo = new THREE.RingGeometry(0.9, 1, 40)
const coneGeo = new THREE.ConeGeometry(0.26, 0.7, 6).rotateX(Math.PI / 2)
const colors = { accent: new THREE.Color(), ok: new THREE.Color(), bad: new THREE.Color(), shot: new THREE.Color(), dim: new THREE.Color() }

function readColors() {
  colors.accent.copy(cssColor('--accent')); colors.ok.copy(cssColor('--ok')); colors.bad.copy(cssColor('--critical'))
  colors.shot.copy(cssColor('--violet')); colors.dim.copy(cssColor('--border-strong'))
  if (!stage) return
  ;(shield.material as THREE.LineBasicMaterial).color.copy(colors.accent)
  ;(shieldFill.material as THREE.MeshBasicMaterial).color.copy(colors.accent)
  ;(orbit.material as THREE.LineBasicMaterial).color.copy(colors.dim)
  for (const l of lanes) (l.mesh.material as THREE.MeshStandardMaterial).color.copy(l.breached ? colors.bad : colors.shot)
}

function populate(s: Stage) {
  s.scene.add(new THREE.AmbientLight(0xffffff, 1.4))
  const key = new THREE.DirectionalLight(0xffffff, 2.4); key.position.set(4, 9, 7); s.scene.add(key)

  core = new THREE.Mesh(new THREE.IcosahedronGeometry(CORE, 1), new THREE.MeshStandardMaterial({ flatShading: true, roughness: 0.3, metalness: 0.2 }))
  coreWire = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(CORE * 1.35, 0)), new THREE.LineBasicMaterial({ transparent: true, opacity: 0.5 }))
  shield = new THREE.LineSegments(new THREE.WireframeGeometry(new THREE.IcosahedronGeometry(SHIELD, 2)), new THREE.LineBasicMaterial({ transparent: true, opacity: 0.12 }))
  shieldFill = new THREE.Mesh(new THREE.SphereGeometry(SHIELD * 0.995, 40, 28), new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.04, depthWrite: false }))
  const pts = Array.from({ length: 97 }, (_, i) => new THREE.Vector3(Math.cos((i / 96) * Math.PI * 2) * RING, 0, Math.sin((i / 96) * Math.PI * 2) * RING))
  orbit = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineDashedMaterial({ dashSize: 0.25, gapSize: 0.25, transparent: true, opacity: 0.6 }))
  orbit.computeLineDistances()
  s.scene.add(core, coreWire, shield, shieldFill, orbit)
  readColors()

  const v = new THREE.Vector3()
  s.onFrame((dt, t) => {
    // 발사 대기열: 일정 간격으로 하나씩
    cooldown -= dt
    while (queue.length && cooldown <= 0) { launch(queue.shift()!); cooldown += props.interval / 1000 }
    if (!queue.length) cooldown = Math.max(cooldown, 0)

    const spin = reducedMotion ? 0 : dt
    core.rotation.y += spin * 0.4; coreWire.rotation.y -= spin * 0.25; coreWire.rotation.x += spin * 0.15; shield.rotation.y += spin * 0.05

    // 돌파 비율만큼 코어가 강조색에서 위험색으로 물든다
    const ratio = stats.fired ? stats.breached / stats.fired : 0
    const base = colors.accent.clone().lerp(colors.bad, Math.min(1, ratio * 1.6))
    const cm = core.material as THREE.MeshStandardMaterial
    coreFlash = Math.max(0, coreFlash - dt * 2.2)
    cm.color.copy(base).lerp(colors.bad, coreFlash)
    cm.emissive.copy(cm.color).multiplyScalar((isDark.value ? 0.35 : 0.12) + coreFlash * 0.8)
    ;(coreWire.material as THREE.LineBasicMaterial).color.copy(cm.color)
    core.scale.setScalar(1 + coreFlash * 0.18 + (reducedMotion ? 0 : Math.sin(t * 2) * 0.02))

    shieldFlash = Math.max(0, shieldFlash - dt * 2.5)
    ;(shield.material as THREE.LineBasicMaterial).opacity = 0.12 + shieldFlash * 0.5
    ;(shieldFill.material as THREE.MeshBasicMaterial).opacity = 0.04 + shieldFlash * 0.12

    for (const l of lanes) {
      l.angle += (l.target - l.angle) * Math.min(1, dt * 4)
      l.mesh.position.set(Math.cos(l.angle) * RING, l.y, Math.sin(l.angle) * RING)
      l.mesh.lookAt(0, 0, 0)
    }
    fx = fx.filter((f) => (f.update(dt) ? true : (f.dispose(), false)))

    // 발사대 이름표를 화면 좌표로 옮긴다
    labels.value = lanes.map((l) => {
      v.copy(l.mesh.position).setY(l.y - 0.75).project(s.camera)
      return { id: l.id, x: (v.x + 1) * 0.5 * s.size.w, y: (1 - v.y) * 0.5 * s.size.h, show: v.z < 1, breached: l.breached }
    })
  })
}

function laneFor(id: string): Lane {
  let l = lanes.find((x) => x.id === id)
  if (l) return l
  const mesh = new THREE.Mesh(coneGeo, new THREE.MeshStandardMaterial({ color: colors.shot, flatShading: true, roughness: 0.4 }))
  l = { id, angle: 0, target: 0, y: 0, mesh, breached: false }
  lanes.push(l); lanes.sort((a, b) => a.id.localeCompare(b.id, undefined, { numeric: true }))
  stage?.scene.add(mesh)
  // 새 발사대가 생기면 전체를 고르게 다시 배치한다
  lanes.forEach((x, i) => {
    x.target = (i / lanes.length) * Math.PI * 2
    x.y = lanes.length > 4 ? (i % 2 ? 0.9 : -0.9) : 0
    if (x === l) x.angle = x.target
  })
  return l
}

function launch(s: Shot) {
  if (!stage) return
  const lane = laneFor(s.lane)
  stats.fired++
  const from = lane.mesh.position.clone()
  const dir = from.clone().negate().normalize()
  // 같은 지점만 맞지 않도록 탄착점을 조금 흩뜨린다
  const jitter = new THREE.Vector3(Math.random() - 0.5, Math.random() - 0.5, Math.random() - 0.5).multiplyScalar(0.9)
  const normal = from.clone().normalize().add(jitter).normalize()
  const hitShield = normal.clone().multiplyScalar(SHIELD)
  const hitCore = normal.clone().multiplyScalar(CORE)
  const to = s.success ? hitCore : hitShield
  const mat = new THREE.MeshBasicMaterial({ color: colors.shot.clone(), transparent: true })
  const ball = new THREE.Mesh(shotGeo, mat)
  const trailPos = new Float32Array(14 * 3)
  const trail = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: colors.shot.clone(), transparent: true, opacity: 0.5 }))
  trail.geometry.setAttribute('position', new THREE.BufferAttribute(trailPos, 3))
  stage.scene.add(ball, trail)
  const flight = s.success ? FLIGHT * 1.15 : FLIGHT
  let t = 0
  let phase: 'fly' | 'out' = 'fly'
  let vel = new THREE.Vector3()
  let crossed = false
  const history: THREE.Vector3[] = []

  fx.push({
    update(dt) {
      t += dt
      if (phase === 'fly') {
        const k = Math.min(1, t / flight)
        ball.position.lerpVectors(from, to, k).y += Math.sin(Math.PI * k) * 1.2
        // 성공한 공격은 방어막을 통과하는 순간 붉게 변하며 균열 파문을 남긴다
        if (s.success && !crossed && ball.position.length() <= SHIELD) {
          crossed = true; mat.color.copy(colors.bad); ripple(ball.position.clone(), colors.bad, 0.6)
        }
        if (k >= 1) {
          phase = 'out'; t = 0
          if (s.success) {
            stats.breached++; lane.breached = true
            ;(lane.mesh.material as THREE.MeshStandardMaterial).color.copy(colors.bad)
            coreFlash = 1; shockwave()
            vel.set(0, 0, 0)
          } else {
            stats.blocked++
            shieldFlash = Math.min(1, shieldFlash + 0.6); mat.color.copy(colors.ok)
            ripple(hitShield, colors.ok, 1)
            vel = dir.clone().reflect(normal).multiplyScalar(6)
          }
        }
      } else {
        ball.position.addScaledVector(vel, dt)
        const a = Math.max(0, 1 - t / 0.45)
        mat.opacity = a; ball.scale.setScalar(s.success ? 1 + t * 4 : 1)
      }
      history.unshift(ball.position.clone()); if (history.length > 14) history.pop()
      history.forEach((p, i) => trailPos.set([p.x, p.y, p.z], i * 3))
      trail.geometry.setDrawRange(0, history.length)
      trail.geometry.attributes.position!.needsUpdate = true
      ;(trail.material as THREE.LineBasicMaterial).color.copy(mat.color)
      ;(trail.material as THREE.LineBasicMaterial).opacity = 0.5 * mat.opacity
      return phase === 'fly' || t < 0.45
    },
    dispose() { stage?.scene.remove(ball, trail); mat.dispose(); trail.geometry.dispose(); (trail.material as THREE.Material).dispose() },
  })
}

// 방어막 표면에 퍼지는 고리
function ripple(at: THREE.Vector3, color: THREE.Color, size: number) {
  if (!stage) return
  const mat = new THREE.MeshBasicMaterial({ color: color.clone(), transparent: true, side: THREE.DoubleSide, depthWrite: false })
  const ring = new THREE.Mesh(ringGeo, mat)
  ring.position.copy(at); ring.lookAt(at.clone().multiplyScalar(2))
  stage.scene.add(ring)
  let t = 0
  fx.push({
    update(dt) { t += dt; ring.scale.setScalar(0.1 + t * 1.5 * size); mat.opacity = Math.max(0, 0.9 - t / 0.35); return t < 0.35 },
    dispose() { stage?.scene.remove(ring); mat.dispose() },
  })
}

// 코어가 뚫렸을 때 퍼지는 충격파
function shockwave() {
  if (!stage) return
  const mat = new THREE.MeshBasicMaterial({ color: colors.bad.clone(), transparent: true, wireframe: true, depthWrite: false })
  const ball = new THREE.Mesh(new THREE.IcosahedronGeometry(CORE, 1), mat)
  stage.scene.add(ball)
  let t = 0
  fx.push({
    update(dt) { t += dt; ball.scale.setScalar(1 + t * 4); mat.opacity = Math.max(0, 0.8 - t / 0.7); return t < 0.7 },
    dispose() { stage?.scene.remove(ball); ball.geometry.dispose(); mat.dispose() },
  })
}

function reset() {
  fx.forEach((f) => f.dispose()); fx = []
  queue = []; consumed = 0; cooldown = 0; coreFlash = 0; shieldFlash = 0
  stats.fired = 0; stats.blocked = 0; stats.breached = 0
  for (const l of lanes) { l.breached = false; (l.mesh.material as THREE.MeshStandardMaterial).color.copy(colors.shot) }
}

function consume() {
  if (props.feed.length < consumed) reset()
  queue.push(...props.feed.slice(consumed)); consumed = props.feed.length
}

onMounted(() => {
  if (!host.value) return
  try {
    stage = createStage(host.value, { distance: 17, height: 8, autoRotate: 0.4 })
  } catch (e) {
    if (e instanceof WebGLUnavailable) { error.value = '이 브라우저에서 WebGL 을 사용할 수 없어 3D 화면을 표시하지 못했습니다.'; return }
    throw e
  }
  populate(stage); consume()
})
onBeforeUnmount(() => { fx.forEach((f) => f.dispose()); stage?.dispose(); shotGeo.dispose(); ringGeo.dispose(); coneGeo.dispose() })
// 배열이 통째로 바뀌면(재생) 처음부터, 뒤에 덧붙으면(실시간) 이어서 발사한다
watch(() => props.feed, () => { reset(); consume() })
watch(() => props.feed.length, consume)
watch(isDark, () => void nextTick(readColors))
</script>

<template>
  <div class="arena" :style="{ height: `${height}px` }">
    <div ref="host" class="canvas" />
    <p v-if="error" class="muted small fallback">{{ error }}</p>
    <span v-for="l in labels" v-show="l.show" :key="l.id" class="lane mono" :class="{ bad: l.breached }" :style="{ transform: `translate(${l.x}px, ${l.y}px) translate(-50%, 0)` }">{{ l.id }}</span>
    <div class="hud">
      <div><span class="muted small">발사</span><b class="num">{{ stats.fired }}</b></div>
      <div class="ok"><span class="muted small">차단</span><b class="num">{{ stats.blocked }}</b></div>
      <div class="bad"><span class="muted small">돌파</span><b class="num">{{ stats.breached }}</b></div>
    </div>
    <div class="legend small"><span><i class="dot ok" />방어막에서 차단</span><span><i class="dot bad" />방어막 돌파 → 코어 피격</span></div>
  </div>
</template>

<style scoped>
.arena { position: relative; border-radius: var(--radius); overflow: hidden; border: 1px solid var(--border);
  background: radial-gradient(ellipse at 50% 45%, var(--surface-2), var(--surface) 70%); }
.canvas { position: absolute; inset: 0; cursor: grab; touch-action: none; }
.canvas:active { cursor: grabbing; }
.fallback { position: absolute; inset: 0; display: grid; place-items: center; padding: 24px; text-align: center; }
.lane { position: absolute; left: 0; top: 0; font-size: 10px; color: var(--muted); pointer-events: none; white-space: nowrap; transition: color .3s; }
.lane.bad { color: var(--critical); font-weight: 600; }
.hud { position: absolute; left: 12px; top: 10px; display: flex; gap: 8px; pointer-events: none; }
.hud > div { display: flex; flex-direction: column; gap: 2px; padding: 6px 10px; border-radius: 9px; background: color-mix(in srgb, var(--surface) 80%, transparent); border: 1px solid var(--border); }
.hud b { font-size: 16px; color: var(--text); }
.hud .ok b { color: var(--ok); }
.hud .bad b { color: var(--critical); }
.legend { position: absolute; right: 12px; bottom: 8px; display: flex; gap: 12px; color: var(--text-2); pointer-events: none; flex-wrap: wrap; justify-content: flex-end; }
.legend span { display: inline-flex; align-items: center; gap: 5px; }
.dot { width: 8px; height: 8px; border-radius: 50%; }
.dot.ok { background: var(--ok); }
.dot.bad { background: var(--critical); }
</style>
