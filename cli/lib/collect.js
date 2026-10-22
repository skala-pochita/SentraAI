// 감사할 파일 모으기. git 저장소면 .gitignore 를 따르고, 아니면 흔한 산출물 디렉터리를 건너뛴다.
// 보안 감사에는 텍스트(소스·설정·의존성 정의)가 필요하므로 큰 바이너리·아주 큰 파일은 빼서 업로드 한도를 지킨다.
import { execFileSync } from 'node:child_process'
import { closeSync, existsSync, lstatSync, openSync, readdirSync, readFileSync, readSync } from 'node:fs'
import { join, relative, sep } from 'node:path'

// 서버 업로드 한도 (backend/core/project_upload.py 와 같은 값)
export const LIMITS = { files: 5000, expandedBytes: 100 * 1024 * 1024, zipBytes: 20 * 1024 * 1024 }
// 바이너리는 이보다 크면 뺀다 (gradle-wrapper.jar 같은 작은 빌드 파일은 격리 실행에 필요할 수 있어 남긴다)
export const MAX_BINARY_BYTES = 512 * 1024
// 텍스트라도 이보다 크면 뺀다 (덤프·번들된 산출물)
export const MAX_FILE_BYTES = 5 * 1024 * 1024
export const IGNORE_FILE = '.sentraaiignore'

// 이름만으로 산출물·캐시라고 볼 수 있는 디렉터리
const SKIP_DIRS = new Set([
  '.git', 'node_modules', '.venv', 'venv', '__pycache__', '.mypy_cache', '.pytest_cache', '.ruff_cache',
  '.next', '.nuxt', '.gradle', '.idea', '.vscode', '.terraform',
])
// 소스 디렉터리 이름으로도 흔한 산출물 이름 (예: Java 패키지 lab/target). 바로 옆에 빌드 파일이 있을 때만 산출물로 본다
const BUILD_OUTPUT_DIRS = new Map([
  ['target', ['pom.xml', 'Cargo.toml', 'build.sbt']],
  ['build', ['build.gradle', 'build.gradle.kts', 'settings.gradle', 'settings.gradle.kts', 'package.json', 'pyproject.toml', 'setup.py', 'CMakeLists.txt']],
  ['dist', ['package.json', 'pyproject.toml', 'setup.py']],
  ['out', ['package.json', 'build.gradle', 'build.gradle.kts']],
  ['coverage', ['package.json', 'pyproject.toml', 'setup.py']],
])

/** (부모 디렉터리의 루트 기준 상대 경로, 디렉터리 이름) → 산출물이면 true. 빌드 파일 확인 결과는 캐시한다 */
export function generatedDirs(root) {
  const cache = new Map()
  return (parent, name) => {
    if (SKIP_DIRS.has(name)) return true
    const manifests = BUILD_OUTPUT_DIRS.get(name)
    if (!manifests) return false
    const key = `${parent}/${name}`
    if (!cache.has(key)) cache.set(key, manifests.some((m) => existsSync(join(root, parent, m))))
    return cache.get(key)
  }
}

const inGeneratedDir = (isGenerated, name) => {
  const parts = name.split('/')
  return parts.slice(0, -1).some((p, i) => isGenerated(parts.slice(0, i).join('/'), p))
}

// 확장자만 보고 바이너리로 판단하는 형식 (나머지는 앞부분에 NUL 바이트가 있는지 본다)
const BINARY_EXT = new Set([
  'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'ico', 'tif', 'tiff', 'psd', 'mp3', 'wav', 'mp4', 'mov', 'avi', 'webm',
  'zip', 'tar', 'gz', 'tgz', 'bz2', 'xz', '7z', 'rar', 'jar', 'war', 'ear', 'class', 'so', 'dylib', 'dll', 'exe', 'bin',
  'pdf', 'woff', 'woff2', 'ttf', 'otf', 'eot', 'onnx', 'pt', 'pth', 'ckpt', 'h5', 'safetensors', 'gguf', 'pkl', 'npy',
  'npz', 'parquet', 'sqlite', 'sqlite3', 'db', 'pyc', 'whl', 'dmg', 'iso',
])

// 실제 키가 들어 있을 수 있는 .env 는 기본 제외 (예시 파일은 포함)
export function isEnvFile(path) {
  const base = path.split('/').pop() ?? ''
  return /^\.env(\..+)?$/.test(base) && !/\.(example|sample|template|dist)$/.test(base)
}

export function isBinary(abs, name) {
  const ext = name.includes('.') ? name.split('.').pop().toLowerCase() : ''
  if (BINARY_EXT.has(ext)) return true
  const buf = Buffer.alloc(8192)
  const fd = openSync(abs, 'r')
  try {
    return buf.subarray(0, readSync(fd, buf, 0, buf.length, 0)).includes(0)
  } finally {
    closeSync(fd)
  }
}

/**
 * .sentraaiignore·--exclude 패턴 (gitignore 의 간단한 부분집합)
 * - `name/`     어느 위치든 그 이름의 디렉터리
 * - `*.mp4`     '/' 가 없으면 파일·디렉터리 이름에 맞춘다
 * - `data/raw`  '/' 가 있으면 루트 기준 경로(그 아래 전부 포함)에 맞춘다. `*` 는 '/' 를 넘지 않고 `**` 는 넘는다
 */
export function ignoreMatcher(patterns) {
  const toRe = (glob) => glob.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*\*/g, '\0').replace(/\*/g, '[^/]*').replace(/\?/g, '[^/]').replace(/\0/g, '.*')
  const rules = patterns.map((p) => p.trim()).filter((p) => p && !p.startsWith('#')).map((p) => {
    const dirOnly = p.endsWith('/')
    const body = p.replace(/^\/|\/$/g, '')
    if (!body.includes('/')) return { part: new RegExp(`^${toRe(body)}$`), dirOnly }
    return { path: new RegExp(`^${toRe(body)}(/.*)?$`) }
  })
  return (name) => rules.some((r) => {
    if (r.path) return r.path.test(name)
    const parts = name.split('/')
    return (r.dirOnly ? parts.slice(0, -1) : parts).some((seg) => r.part.test(seg))
  })
}

function gitFiles(root) {
  try {
    execFileSync('git', ['-C', root, 'rev-parse', '--is-inside-work-tree'], { stdio: 'ignore' })
  } catch {
    return null // git 저장소가 아니거나 git 이 없음 → 직접 탐색
  }
  const out = execFileSync('git', ['-C', root, 'ls-files', '-co', '--exclude-standard', '-z', '--', '.'], { maxBuffer: 64 * 1024 * 1024 })
  return out.toString('utf8').split('\0').filter(Boolean)
}

function walk(root, isGenerated, dir = root, acc = []) {
  for (const d of readdirSync(dir, { withFileTypes: true })) {
    if (d.isDirectory()) {
      if (!isGenerated(relative(root, dir).split(sep).join('/'), d.name)) walk(root, isGenerated, join(dir, d.name), acc)
    } else if (d.isFile() || d.isSymbolicLink()) {
      acc.push(relative(root, join(dir, d.name)).split(sep).join('/'))
    }
  }
  return acc
}

/**
 * @returns {{
 *   files: { name: string, size: number, mtime: Date }[], bytes: number,
 *   skipped: { env: string[], symlinks: number, generated: number, ignored: number, large: { name: string, size: number, binary: boolean }[] }
 * }}
 */
export function collect(root, { includeEnv = false, exclude = [] } = {}) {
  const ignoreFile = join(root, IGNORE_FILE)
  const ignored = ignoreMatcher([...(existsSync(ignoreFile) ? readFileSync(ignoreFile, 'utf8').split('\n') : []), ...exclude])
  const isGenerated = generatedDirs(root)
  const names = gitFiles(root) ?? walk(root, isGenerated)
  const files = []
  const skipped = { env: [], symlinks: 0, generated: 0, ignored: 0, large: [] }
  let bytes = 0
  for (const name of names) {
    if (inGeneratedDir(isGenerated, name)) { skipped.generated++; continue }
    if (ignored(name)) { skipped.ignored++; continue }
    if (!includeEnv && isEnvFile(name)) { skipped.env.push(name); continue }
    const abs = join(root, name)
    let st
    try {
      st = lstatSync(abs)
    } catch {
      continue // git 목록에는 있으나 지워진 파일
    }
    // 서버가 심볼릭 링크를 거부하므로 미리 뺀다
    if (st.isSymbolicLink()) { skipped.symlinks++; continue }
    if (!st.isFile()) continue
    if (st.size > MAX_FILE_BYTES) { skipped.large.push({ name, size: st.size, binary: isBinary(abs, name) }); continue }
    if (st.size > MAX_BINARY_BYTES && isBinary(abs, name)) { skipped.large.push({ name, size: st.size, binary: true }); continue }
    files.push({ name, size: st.size, mtime: st.mtime })
    bytes += st.size
  }
  files.sort((a, b) => a.name.localeCompare(b.name))
  skipped.large.sort((a, b) => b.size - a.size)
  return { files, skipped, bytes }
}
