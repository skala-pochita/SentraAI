// Docker Desktop 확인과 SentraAI 로컬 스택(docker compose) 준비.
// 저장소 없이 동작한다: 패키지에 든 compose.yml 을 ~/.sentraai 로 복사하고, 배포 이미지를 이름으로 받아 띄운다.
import { execFile, spawn } from 'node:child_process'
import { copyFileSync, cpSync, existsSync, mkdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { promisify } from 'node:util'
import { configDir } from './config.js'
import { CliError } from './errors.js'

const run = promisify(execFile)
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const PKG_DIR = join(dirname(fileURLToPath(import.meta.url)), '..')
export const VERSION = JSON.parse(readFileSync(join(PKG_DIR, 'package.json'), 'utf8')).version

// 이미지 위치. 개발 중에는 SENTRA_REGISTRY·SENTRA_VERSION 으로 바꿀 수 있다.
export const stackEnv = () => ({
  ...process.env,
  SENTRA_REGISTRY: process.env.SENTRA_REGISTRY ?? 'ghcr.io/skala-pochita',
  SENTRA_VERSION: process.env.SENTRA_VERSION ?? VERSION,
  SENTRA_PORT: process.env.SENTRA_PORT ?? '5180',
})
export const OWN_IMAGES = ['backend', 'frontend', 'target-agent']
export const imageName = (svc, env = stackEnv()) => `${env.SENTRA_REGISTRY}/sentraai-${svc}:${env.SENTRA_VERSION}`

async function dockerReady() {
  try {
    await run('docker', ['info', '--format', '{{.ServerVersion}}'], { timeout: 15_000 })
    return true
  } catch {
    return false
  }
}

function startDockerDesktop() {
  const [cmd, args] = process.platform === 'darwin' ? ['open', ['-a', 'Docker']]
    : process.platform === 'win32' ? [join(process.env.ProgramFiles ?? 'C:\\Program Files', 'Docker', 'Docker', 'Docker Desktop.exe'), []]
      : ['systemctl', ['--user', 'start', 'docker-desktop']]
  spawn(cmd, args, { stdio: 'ignore', detached: true }).on('error', () => {}).unref()
}

/**
 * Docker 가 꺼져 있으면 켜 달라고 안내하고 자동으로 켜기도 시도한다.
 * 부팅을 기다리는 동안 다른 일(키 입력 등)을 할 수 있게, 기다리는 함수를 돌려준다.
 */
export async function beginDocker(log, timeoutMs = 180_000) {
  if (await dockerReady()) return async () => {}
  try {
    await run('docker', ['--version'], { timeout: 10_000 })
  } catch {
    throw new CliError('Docker 가 설치되어 있지 않습니다.', 'Docker Desktop 을 설치하세요: https://www.docker.com/products/docker-desktop/')
  }
  log('Docker Desktop 을 켜 주세요. 자동으로 켜는 중입니다…')
  startDockerDesktop()
  return async () => {
    const until = Date.now() + timeoutMs
    while (Date.now() < until) {
      if (await dockerReady()) return
      await sleep(3000)
    }
    throw new CliError(`Docker 가 ${timeoutMs / 1000}초 안에 응답하지 않았습니다.`, 'Docker Desktop 을 직접 켠 뒤 다시 실행하세요.')
  }
}

function compose(args, { inherit = false, timeout = 0 } = {}) {
  const dir = configDir()
  const full = ['compose', '-f', join(dir, 'compose.yml'), '--project-directory', dir, ...args]
  return new Promise((resolve) => {
    const tail = []
    const p = spawn('docker', full, { env: stackEnv(), stdio: ['ignore', inherit ? 'inherit' : 'pipe', inherit ? 'inherit' : 'pipe'] })
    const keep = (b) => { tail.push(...b.toString().split('\n').filter(Boolean)); tail.splice(0, Math.max(0, tail.length - 15)) }
    p.stdout?.on('data', keep); p.stderr?.on('data', keep)
    const timer = timeout ? setTimeout(() => p.kill(), timeout) : null
    p.on('error', (e) => { tail.push(String(e)); resolve({ code: 1, tail }) })
    p.on('close', (code) => { if (timer) clearTimeout(timer); resolve({ code: code ?? 1, tail }) })
  })
}

export const webUrl = () => `http://localhost:${stackEnv().SENTRA_PORT}`
export const apiBase = () => `http://127.0.0.1:${stackEnv().SENTRA_PORT}/api/v1`

export async function stackHealthy() {
  try {
    const r = await fetch(`${apiBase()}/ops/health`, { signal: AbortSignal.timeout(3000) })
    return r.ok
  } catch {
    return false
  }
}

async function missingImages() {
  const missing = []
  for (const svc of OWN_IMAGES) {
    try {
      await run('docker', ['image', 'inspect', imageName(svc)], { timeout: 15_000 })
    } catch {
      missing.push(imageName(svc))
    }
  }
  return missing
}

/** 패키지의 compose 파일을 ~/.sentraai 로 복사해 CLI 버전과 맞춘다 */
function syncCompose() {
  mkdirSync(configDir(), { recursive: true, mode: 0o700 })
  copyFileSync(join(PKG_DIR, 'compose.yml'), join(configDir(), 'compose.yml'))
  const sourceDir = process.env.COMPLIANCE_SOURCES_DIR ?? join(configDir(), 'compliance', 'sources')
  mkdirSync(sourceDir, { recursive: true, mode: 0o700 })
  if (!existsSync(join(sourceDir, 'MANIFEST.yaml'))) copyFileSync(join(PKG_DIR, 'resources', 'MANIFEST.yaml'), join(sourceDir, 'MANIFEST.yaml'))
  if (!existsSync(join(sourceDir, 'evidence_policy.yaml'))) copyFileSync(join(PKG_DIR, 'resources', 'evidence_policy.yaml'), join(sourceDir, 'evidence_policy.yaml'))
  const complianceDir = process.env.COMPLIANCE_CONFIG_DIR ?? join(configDir(), 'compliance', 'config')
  mkdirSync(complianceDir, { recursive: true, mode: 0o700 })
  cpSync(join(PKG_DIR, 'resources', 'compliance'), complianceDir, { recursive: true, force: false, errorOnExist: false })
}

async function containerIds() {
  try {
    const { stdout } = await run('docker', ['compose', '-p', 'sentraai', 'ps', '-q'], { timeout: 15_000 })
    return stdout.split('\n').filter(Boolean).sort().join(',')
  } catch {
    return '' // 비교 실패는 '바뀜' 으로 본다
  }
}

/**
 * 실행 중이어도 매번 compose up 을 한다. 이미지 버전(CLI 업그레이드)이나 .env(키 입력·변경)가 바뀐 서비스만
 * compose 가 다시 만들고, 바뀐 게 없으면 그대로 둔다.
 * @returns {Promise<'started' | 'updated' | 'running'>}
 */
export async function ensureStack(log) {
  syncCompose()
  const healthy = await stackHealthy()
  const before = healthy ? await containerIds() : ''
  const missing = await missingImages()
  if (missing.length) {
    log('SentraAI 서버 이미지를 내려받습니다… (처음 한 번, 몇 분 걸려요)')
    const r = await compose(['pull'], { inherit: true })
    if (r.code !== 0) {
      throw new CliError(`이미지를 받지 못했습니다: ${missing.join(', ')}`, '인터넷 연결을 확인하세요. 이미지가 공개(Public)인지도 확인하세요.')
    }
  }
  if (!healthy) log('SentraAI 서버를 시작합니다…')
  const r = await compose(['up', '-d', '--wait', '--remove-orphans'], { timeout: 600_000 })
  if (r.code !== 0) throw new CliError('서버 시작에 실패했습니다.\n' + r.tail.join('\n'), '`sentraai logs` 로 로그를 확인하세요.')
  if (!healthy) return 'started'
  return (await containerIds()) === before ? 'running' : 'updated'
}

/** 로컬 사용자와 보안 기준 문서 색인 (멱등) */
export async function seedLocal() {
  const r = await compose(['exec', '-T', 'api', 'python', '-m', 'api.seed', '--local'], { timeout: 300_000 })
  if (r.code !== 0) throw new CliError('보안 기준 문서 색인에 실패했습니다.\n' + r.tail.join('\n'))
}

// 멈추기·로그는 설정 파일 없이도 되도록 compose 프로젝트 이름으로 찾는다
function byProject(args) {
  return new Promise((resolve) => {
    const p = spawn('docker', ['compose', '-p', 'sentraai', ...args], { stdio: 'inherit' })
    p.on('error', () => resolve(1))
    p.on('close', (code) => resolve(code ?? 1))
  })
}

export async function stopStack() {
  if ((await byProject(['down'])) !== 0) throw new CliError('서버를 멈추지 못했습니다.')
}

export async function showLogs() {
  await byProject(['logs', '--tail', '200'])
}
