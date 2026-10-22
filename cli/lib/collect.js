// 감사할 파일 모으기. git 저장소면 .gitignore 를 따르고, 아니면 흔한 산출물 디렉터리를 건너뛴다.
import { execFileSync } from 'node:child_process'
import { lstatSync, readdirSync } from 'node:fs'
import { join, relative, sep } from 'node:path'

// 서버 업로드 한도 (backend/core/project_upload.py 와 같은 값)
export const LIMITS = { files: 5000, expandedBytes: 100 * 1024 * 1024, zipBytes: 20 * 1024 * 1024 }

const SKIP_DIRS = new Set([
  '.git', 'node_modules', '.venv', 'venv', '__pycache__', '.mypy_cache', '.pytest_cache', '.ruff_cache',
  'dist', 'build', 'target', 'out', '.next', '.nuxt', '.gradle', '.idea', '.vscode', 'coverage', '.terraform',
])

// 실제 키가 들어 있을 수 있는 .env 는 기본 제외 (예시 파일은 포함)
export function isEnvFile(path) {
  const base = path.split('/').pop() ?? ''
  return /^\.env(\..+)?$/.test(base) && !/\.(example|sample|template|dist)$/.test(base)
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

function walk(root, dir = root, acc = []) {
  for (const d of readdirSync(dir, { withFileTypes: true })) {
    if (d.isDirectory()) {
      if (!SKIP_DIRS.has(d.name)) walk(root, join(dir, d.name), acc)
    } else if (d.isFile() || d.isSymbolicLink()) {
      acc.push(relative(root, join(dir, d.name)).split(sep).join('/'))
    }
  }
  return acc
}

/**
 * @returns {{ files: { name: string, size: number, mtime: Date }[], skipped: { env: string[], symlinks: number, generated: number }, bytes: number }}
 */
export function collect(root, { includeEnv = false } = {}) {
  const names = gitFiles(root) ?? walk(root)
  const files = []
  const skipped = { env: [], symlinks: 0, generated: 0 }
  let bytes = 0
  for (const name of names) {
    if (name.split('/').some((p) => SKIP_DIRS.has(p))) { skipped.generated++; continue }
    if (!includeEnv && isEnvFile(name)) { skipped.env.push(name); continue }
    let st
    try {
      st = lstatSync(join(root, name))
    } catch {
      continue // git 목록에는 있으나 지워진 파일
    }
    // 서버가 심볼릭 링크를 거부하므로 미리 뺀다
    if (st.isSymbolicLink()) { skipped.symlinks++; continue }
    if (!st.isFile()) continue
    files.push({ name, size: st.size, mtime: st.mtime })
    bytes += st.size
  }
  files.sort((a, b) => a.name.localeCompare(b.name))
  return { files, skipped, bytes }
}
