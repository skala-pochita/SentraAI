#!/usr/bin/env node
// sentraai — 현재(또는 지정한) 디렉터리를 SentraAI 로 보안 감사하고, 끝나면 대시보드를 연다.
// 로컬 모드로 동작한다: 이 컴퓨터의 Docker 에 서버를 띄우고, 로그인 없이 'User' 로 쓴다.
import { spawn } from 'node:child_process'
import { existsSync, readFileSync, statSync } from 'node:fs'
import { basename, join, resolve } from 'node:path'
import { parseArgs } from 'node:util'
import { client } from '../lib/api.js'
import { collect, LIMITS } from '../lib/collect.js'
import { ask, askSecret, checkOpenAIKey, envPath, mask, readConfig, writeConfig } from '../lib/config.js'
import { apiBase, beginDocker, ensureStack, seedLocal, showLogs, stackHealthy, stopStack, VERSION, webUrl } from '../lib/docker.js'
import { CliError } from '../lib/errors.js'
import { buildZip } from '../lib/zip.js'

const HELP = `사용법
  sentraai [디렉터리]     디렉터리(기본: 현재 위치)를 보안 감사하고 대시보드를 엽니다
  sentraai setup         OpenAI·LangSmith 키를 다시 입력합니다
  sentraai open          대시보드를 엽니다
  sentraai stop          SentraAI 서버를 멈춥니다 (감사 기록은 유지)
  sentraai logs          서버 로그를 봅니다

옵션
  -y, --yes            확인 질문 없이 바로 감사
  -m, --mode <모드>     code | full  (기본: 에이전트가 감지되고 실행 준비가 되면 full 을 제안, 아니면 code)
      --include-env    .env 파일도 포함 (기본은 실제 키 보호를 위해 제외)
      --no-open        끝나도 대시보드를 열지 않음
  -v, --version        버전
  -h, --help           도움말

설정 파일: ~/.sentraai/.env`

const c = process.stdout.isTTY && !process.env.NO_COLOR
const paint = (code) => (s) => (c ? `\x1b[${code}m${s}\x1b[0m` : String(s))
const bold = paint(1), dim = paint(2), red = paint(31), green = paint(32), yellow = paint(33), cyan = paint(36)
const SEV_PAINT = { CRITICAL: red, HIGH: yellow, MEDIUM: yellow, LOW: cyan, INFO: dim }

const step = (s) => console.log(`${cyan('›')} ${s}`)
const ok = (s) => console.log(`${green('✔')} ${s}`)
const warn = (s) => console.log(`${yellow('!')} ${s}`)
const mb = (n) => `${(n / 1024 / 1024).toFixed(1)}MB`

function openUrl(url) {
  const [cmd, args] = process.platform === 'darwin' ? ['open', [url]] : process.platform === 'win32' ? ['cmd', ['/c', 'start', '', url]] : ['xdg-open', [url]]
  spawn(cmd, args, { stdio: 'ignore', detached: true }).on('error', () => {}).unref()
}

// 1. 키 입력: OpenAI(필수에 가깝다 — 없으면 정적 규칙만) + LangSmith(선택). Enter 는 기존 값 유지.
async function setup() {
  const prev = readConfig() ?? {}
  let openai = process.env.SENTRA_OPENAI_API_KEY
  for (let i = 0; openai === undefined && i < 3; i++) {
    const keep = prev.OPENAI_API_KEY ? dim(` (Enter = 기존 ${mask(prev.OPENAI_API_KEY)} 유지)`) : ''
    const v = (await askSecret(`${yellow('?')} OpenAI API 키${keep}: `)) || prev.OPENAI_API_KEY || ''
    if (!v) {
      if (await ask(`${yellow('?')} 키 없이 진행할까요? LLM 조사 없이 정적 규칙으로만 감사합니다.`, false)) openai = ''
      continue
    }
    const st = await checkOpenAIKey(v)
    if (st === 'invalid') { console.log(`${red('✖')} OpenAI 가 거부한 키입니다. 다시 입력하세요.`); continue }
    if (st === 'ok') ok('OpenAI 키 확인')
    else warn('네트워크 문제로 키를 확인하지 못했어요. 저장하고 계속합니다.')
    openai = v
  }
  if (openai === undefined) throw new CliError('유효한 OpenAI 키를 받지 못했습니다.', '`sentraai setup` 으로 다시 입력하세요.')
  let langsmith = process.env.SENTRA_LANGSMITH_API_KEY
  if (langsmith === undefined) {
    const keep = prev.LANGSMITH_API_KEY ? `Enter = 기존 ${mask(prev.LANGSMITH_API_KEY)} 유지` : 'Enter = 건너뛰기'
    langsmith = (await askSecret(`${yellow('?')} LangSmith API 키 ${dim(`(선택, ${keep})`)}: `)) || prev.LANGSMITH_API_KEY || ''
  }
  writeConfig({ openai, langsmith }, prev)
  ok(`설정 저장 ${dim(envPath())}`)
}

async function ensureKnowledge(api) {
  const docs = await api.documents()
  if ((Array.isArray(docs) ? docs : docs.items ?? []).length) return
  step('보안 기준 문서를 색인합니다… (처음 한 번)')
  await seedLocal()
}

async function chooseMode(api, target, forced, yes) {
  if (forced) return forced
  if (!target.linked_target_id) return 'code'
  const rt = target.config?.runtime
  if (!rt?.supported) {
    console.log(dim(`  에이전트가 감지됐지만 격리 실행을 지원하지 않아 코드 검사만 합니다: ${rt?.reason ?? '사유 미상'}`))
    return 'code'
  }
  const r = await api.readiness(target.id)
  if (!r.readiness?.ready) {
    console.log(dim('  에이전트가 감지됐지만 실행 설정이 준비되지 않아 코드 검사만 합니다.'))
    console.log(dim('  전체 검사(레드팀 공격 포함)는 대시보드 → 감사 실행 → 실행 설정에서 준비한 뒤 할 수 있어요.'))
    return 'code'
  }
  return yes || (await ask(`${yellow('?')} 에이전트가 감지됐어요. 격리 실행 레드팀 공격까지 포함한 전체 검사를 할까요?`)) ? 'full' : 'code'
}

const DONE = new Set(['COMPLETED', 'FAILED', 'CANCELLED'])
async function follow(api, id) {
  let last = ''
  const tty = process.stdout.isTTY
  const onInt = () => {
    if (tty) process.stdout.write('\n')
    console.log(`\n감사는 서버에서 계속 진행됩니다: ${webUrl()}/audits/${id}`)
    process.exit(130)
  }
  process.once('SIGINT', onInt)
  try {
    for (;;) {
      const a = await api.audit(id)
      const pct = Math.round((a.progress ?? 0) * 100)
      const bar = '█'.repeat(Math.round(pct / 5)).padEnd(20, '░')
      const line = `  ${bar} ${String(pct).padStart(3)}%  ${a.status}${a.current_node ? ` · ${a.current_node}` : ''}`
      if (tty) process.stdout.write(`\r\x1b[2K${line}`)
      else if (line !== last) console.log(line)
      last = line
      if (DONE.has(a.status)) { if (tty) process.stdout.write('\n'); return a }
      await new Promise((r) => setTimeout(r, 2000))
    }
  } finally {
    process.off('SIGINT', onInt)
  }
}

async function startServer(api) {
  const started = await ensureStack(step)
  ok(started ? 'SentraAI 서버 준비 완료' : 'SentraAI 서버 실행 중')
  const { mode } = await api.mode()
  if (mode !== 'local') throw new CliError(`포트 ${new URL(webUrl()).port} 에서 로컬 모드가 아닌 SentraAI 가 응답합니다.`, 'SENTRA_PORT 로 다른 포트를 지정하세요.')
  await ensureKnowledge(api)
}

async function audit(values, dirArg) {
  const dir = resolve(dirArg ?? process.cwd())
  if (!existsSync(dir) || !statSync(dir).isDirectory()) throw new CliError(`디렉터리가 없습니다: ${dir}`)

  // Docker 가 꺼져 있으면 먼저 켜 두고(부팅에 시간이 걸림), 그동안 키를 입력받는다
  const waitDocker = await beginDocker(step)
  if (!readConfig()) {
    console.log(`처음 실행이라 키를 설정합니다. ${dim('(나중에 `sentraai setup` 으로 바꿀 수 있어요)')}`)
    await setup()
    console.log('')
  }

  const { files, skipped, bytes } = collect(dir, { includeEnv: values['include-env'] })
  if (!files.length) throw new CliError('감사할 파일이 없습니다.')
  console.log(`  대상   ${bold(dir)}`)
  console.log(`  파일   ${files.length}개 · ${mb(bytes)}`)
  if (skipped.env.length) console.log(`  제외   ${skipped.env.join(', ')} ${dim('(실제 키 보호 — 포함하려면 --include-env)')}`)
  if (skipped.symlinks) console.log(`  제외   심볼릭 링크 ${skipped.symlinks}개`)
  if (files.length > LIMITS.files) throw new CliError(`파일이 너무 많습니다 (${files.length} > ${LIMITS.files}).`, '.gitignore 로 산출물·의존성을 제외하세요.')
  if (bytes > LIMITS.expandedBytes) throw new CliError(`프로젝트가 너무 큽니다 (${mb(bytes)} > ${mb(LIMITS.expandedBytes)}).`, '.gitignore 로 산출물·의존성을 제외하세요.')
  console.log('')
  if (!values.yes && !(await ask(`${yellow('?')} 이 디렉터리를 보안 감사할까요?`))) return console.log(dim('취소했습니다.'))

  await waitDocker()
  ok('Docker 실행 중')
  const api = client(apiBase())
  await startServer(api)

  const zip = buildZip(files.map((f) => ({ name: f.name, data: readFileSync(join(dir, f.name)), mtime: f.mtime })))
  if (zip.length > LIMITS.zipBytes) throw new CliError(`압축 결과가 업로드 한도를 넘습니다 (${mb(zip.length)} > ${mb(LIMITS.zipBytes)}).`)
  step(`업로드 중… (${mb(zip.length)})`)
  const target = await api.upload(zip, `${basename(dir) || 'project'}.zip`)
  ok(`업로드 완료 ${dim(target.id.slice(0, 8))}`)

  const mode = await chooseMode(api, target, values.mode, values.yes)
  const ids = [target.id, ...(mode === 'full' && target.linked_target_id ? [target.linked_target_id] : [])]
  const { audit_id: id } = await api.startAudit(ids, mode)
  ok(`감사 시작 ${dim(`${id.slice(0, 8)} · ${mode === 'full' ? '전체(코드 + 에이전트)' : '코드'} 모드`)}`)
  const a = await follow(api, id)

  const url = `${webUrl()}/audits/${id}${a.status === 'COMPLETED' ? '/findings' : ''}`
  console.log('')
  if (a.status === 'COMPLETED') {
    const s = a.summary?.by_severity ?? {}
    const parts = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO'].filter((k) => s[k]).map((k) => SEV_PAINT[k](`${k} ${s[k]}`))
    ok(`감사 완료 — 발견 ${a.summary?.total ?? 0}건${parts.length ? `  ${parts.join(dim(' · '))}` : ''}`)
  } else {
    console.log(`${red('✖')} 감사 ${a.status === 'FAILED' ? '실패' : '취소'} — 자세한 내용은 대시보드에서 확인하세요.`)
    process.exitCode = 1
  }
  console.log(`  대시보드  ${cyan(url)}`)
  if (!values['no-open']) openUrl(url)
}

async function main() {
  const { values, positionals } = parseArgs({
    allowPositionals: true,
    options: {
      yes: { type: 'boolean', short: 'y' }, mode: { type: 'string', short: 'm' }, help: { type: 'boolean', short: 'h' },
      version: { type: 'boolean', short: 'v' }, 'include-env': { type: 'boolean' }, 'no-open': { type: 'boolean' },
    },
  })
  if (values.help) return console.log(HELP)
  if (values.version) return console.log(VERSION)
  if (values.mode && !['code', 'full'].includes(values.mode)) throw new CliError(`알 수 없는 모드: ${values.mode}`, 'code 또는 full 을 지정하세요.')

  const [cmd, ...rest] = positionals
  if (cmd === 'stop') return stopStack()
  if (cmd === 'logs') return showLogs()
  if (cmd === 'open') {
    if (!(await stackHealthy())) throw new CliError('SentraAI 서버가 실행 중이 아닙니다.', '`sentraai` 로 감사를 시작하면 서버가 함께 켜져요.')
    return openUrl(webUrl())
  }

  console.log(`\n${bold('SentraAI')} ${dim(`v${VERSION} · AI 에이전트·코드 보안 감사`)}\n`)
  if (cmd === 'setup') {
    await setup()
    // 서버가 떠 있으면 새 키로 컨테이너를 다시 만든다
    if (await stackHealthy()) { await ensureStack(step, { force: true }); ok('새 설정을 서버에 반영했습니다') }
    return
  }
  return audit(values, cmd === 'audit' ? rest[0] : cmd)
}

main().catch((e) => {
  if (e instanceof CliError) {
    console.error(`\n${red('✖')} ${e.message}`)
    if (e.details?.criteria) console.error(dim(JSON.stringify(e.details.criteria, null, 1)))
    if (e.hint) console.error(dim(`  ${e.hint}`))
  } else {
    console.error(e)
  }
  process.exitCode = 1
})
