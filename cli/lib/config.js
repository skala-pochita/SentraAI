// 사용자 설정: ~/.sentraai/.env (OpenAI·LangSmith 키 + 자동 생성 비밀값). 본인만 읽을 수 있게 저장한다.
import { randomBytes } from 'node:crypto'
import { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { createInterface } from 'node:readline/promises'
import { CliError } from './errors.js'

export const configDir = () => process.env.SENTRA_CONFIG_DIR ?? join(homedir(), '.sentraai')
export const envPath = () => join(configDir(), '.env')

// 서버 기본값 (저장소 .env.example 과 같은 값)
const DEFAULTS = {
  LLM_SMALL_MODEL: 'gpt-5.4-nano',
  EMBEDDING_MODEL: 'text-embedding-3-small',
  LLM_PROXY_MODEL_ALLOWLIST: 'gpt-5.4-nano',
  PROBE_ATTEMPTS: '3',
}

export function parseEnv(text) {
  const out = {}
  for (const raw of text.split('\n')) {
    const line = raw.trim()
    if (!line || line.startsWith('#') || !line.includes('=')) continue
    out[line.slice(0, line.indexOf('=')).trim()] = line.slice(line.indexOf('=') + 1).trim()
  }
  return out
}

export function readConfig() {
  return existsSync(envPath()) ? parseEnv(readFileSync(envPath(), 'utf8')) : null
}

/** 키를 저장한다. 비밀값(JWT·서비스 토큰)은 처음 한 번 무작위로 만들고 이후 유지한다. */
export function writeConfig(keys, prev = readConfig() ?? {}) {
  const cfg = {
    ...DEFAULTS,
    ...prev,
    OPENAI_API_KEY: keys.openai ?? '',
    LANGSMITH_API_KEY: keys.langsmith ?? '',
    JWT_SECRET: prev.JWT_SECRET || randomBytes(32).toString('hex'),
    MCP_SERVICE_TOKEN: prev.MCP_SERVICE_TOKEN || randomBytes(24).toString('hex'),
  }
  mkdirSync(configDir(), { recursive: true, mode: 0o700 })
  const body = ['# sentraai CLI 설정 — `sentraai setup` 으로 다시 입력할 수 있어요. 이 파일을 공유하지 마세요.',
    ...Object.entries(cfg).map(([k, v]) => `${k}=${v}`)].join('\n') + '\n'
  writeFileSync(envPath(), body, { mode: 0o600 })
  chmodSync(envPath(), 0o600) // 이미 있던 파일도 권한을 좁힌다
  return cfg
}

// 키 앞 4자만 보여 준다 (harness/masking.py 의 mask_secret 과 같은 규칙)
export const mask = (v) => (v ? `${v.slice(0, 4)}****` : '(없음)')

export async function ask(question, def = true) {
  if (!process.stdin.isTTY) return def
  const rl = createInterface({ input: process.stdin, output: process.stdout })
  try {
    const a = (await rl.question(`${question} ${def ? '(Y/n)' : '(y/N)'} `)).trim().toLowerCase()
    return a ? ['y', 'yes', 'ㅛ'].includes(a) : def
  } finally {
    rl.close()
  }
}

/** 입력한 글자를 화면에 보이지 않게 받는다 (붙여넣기 가능) */
export function askSecret(question) {
  const stdin = process.stdin
  if (!stdin.isTTY) throw new CliError('키를 입력받으려면 터미널에서 실행하세요.', 'SENTRA_OPENAI_API_KEY 환경변수로 넣을 수도 있어요.')
  process.stdout.write(question)
  return new Promise((resolve) => {
    let buf = ''
    const done = () => {
      stdin.off('data', onData); stdin.setRawMode(false); stdin.pause()
      process.stdout.write('\n')
      resolve(buf.trim())
    }
    const onData = (s) => {
      for (const ch of s) {
        if (ch === '\r' || ch === '\n') return done()
        if (ch === '\u0003') { stdin.setRawMode(false); process.stdout.write('\n'); process.exit(130) }
        if (ch === '\u007f' || ch === '\b') { if (buf) { buf = buf.slice(0, -1); process.stdout.write('\b \b') } continue }
        if (ch >= ' ') { buf += ch; process.stdout.write('*') }
      }
    }
    stdin.setRawMode(true); stdin.setEncoding('utf8'); stdin.resume()
    stdin.on('data', onData)
  })
}

/** OpenAI 키가 유효한지 확인한다. 키는 OpenAI 에만 보낸다. 네트워크 문제는 'unknown'. */
export async function checkOpenAIKey(key) {
  try {
    const r = await fetch('https://api.openai.com/v1/models', { headers: { Authorization: `Bearer ${key}` }, signal: AbortSignal.timeout(10_000) })
    if (r.ok) return 'ok'
    return r.status === 401 ? 'invalid' : 'unknown'
  } catch {
    return 'unknown'
  }
}
