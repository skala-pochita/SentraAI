// three.js 공용 무대: 렌더러·카메라·조작·크기 추적·화면 밖 일시정지·정리를 한 곳에서 맡는다.
// 이 모듈은 3D 화면에서만 동적 import 되므로 첫 화면 JS 예산에 들어가지 않는다.
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

export const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches

// three 는 CSS 변수를 읽지 못하므로 현재 테마의 토큰 값을 직접 꺼낸다
export function cssColor(name: string, fallback = '#94a3b8'): THREE.Color {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return new THREE.Color(v || fallback)
}

export const SEV_VAR: Record<string, string> = {
  CRITICAL: '--critical', HIGH: '--high', MEDIUM: '--medium', LOW: '--low', INFO: '--info',
}

// 레드팀 화면의 공격 시도 하나 (lane = 기본 probe id)
export interface Shot { lane: string; success: boolean }

export interface Stage {
  scene: THREE.Scene
  camera: THREE.PerspectiveCamera
  renderer: THREE.WebGLRenderer
  controls: OrbitControls
  size: { w: number; h: number }
  onFrame(cb: (dt: number, t: number) => void): void
  dispose(): void
}

export class WebGLUnavailable extends Error {}

export function createStage(host: HTMLElement, opts: { distance: number; height?: number; autoRotate?: number }): Stage {
  let renderer: THREE.WebGLRenderer
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'low-power' })
  } catch (e) {
    throw new WebGLUnavailable(e instanceof Error ? e.message : String(e))
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  renderer.setClearColor(0x000000, 0)
  host.appendChild(renderer.domElement)

  const scene = new THREE.Scene()
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 200)
  camera.position.set(0, opts.height ?? opts.distance * 0.35, opts.distance)

  const controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.enablePan = false
  controls.minDistance = opts.distance * 0.45
  controls.maxDistance = opts.distance * 1.8
  controls.autoRotate = !reducedMotion && (opts.autoRotate ?? 0) > 0
  controls.autoRotateSpeed = opts.autoRotate ?? 0

  const size = { w: 1, h: 1 }
  const resize = () => {
    size.w = Math.max(1, host.clientWidth); size.h = Math.max(1, host.clientHeight)
    renderer.setSize(size.w, size.h, false)
    renderer.domElement.style.width = '100%'; renderer.domElement.style.height = '100%'
    camera.aspect = size.w / size.h; camera.updateProjectionMatrix()
  }
  const ro = new ResizeObserver(resize)
  ro.observe(host)
  resize()

  const frames: ((dt: number, t: number) => void)[] = []
  const clock = new THREE.Clock()
  const loop = () => {
    const dt = Math.min(clock.getDelta(), 0.05)
    const t = clock.elapsedTime
    for (const f of frames) f(dt, t)
    controls.update()
    renderer.render(scene, camera)
  }
  // 화면 밖에 있으면 렌더 루프를 멈춰 GPU 를 쉬게 한다
  const io = new IntersectionObserver(([e]) => {
    if (e?.isIntersecting) { clock.getDelta(); renderer.setAnimationLoop(loop) } else renderer.setAnimationLoop(null)
  })
  io.observe(host)

  return {
    scene, camera, renderer, controls, size,
    onFrame: (cb) => { frames.push(cb) },
    dispose() {
      renderer.setAnimationLoop(null)
      io.disconnect(); ro.disconnect(); controls.dispose()
      scene.traverse((o) => {
        const m = o as THREE.Mesh
        m.geometry?.dispose()
        const mat = m.material as THREE.Material | THREE.Material[] | undefined
        if (Array.isArray(mat)) mat.forEach((x) => x.dispose()); else mat?.dispose()
      })
      renderer.dispose()
      renderer.domElement.remove()
    },
  }
}
