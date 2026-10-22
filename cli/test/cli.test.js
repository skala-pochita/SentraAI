import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, readFileSync, statSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { collect, ignoreMatcher, isEnvFile, MAX_BINARY_BYTES, MAX_FILE_BYTES } from '../lib/collect.js'
import { buildZip } from '../lib/zip.js'

// 서버는 Python zipfile 로 푼다 — 같은 라이브러리로 열리는지 확인
const PY = `
import io, json, sys, zipfile
z = zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read()))
assert z.testzip() is None
print(json.dumps({i.filename: z.read(i).decode() for i in z.infolist()}))
`

test('buildZip 결과를 Python zipfile 이 그대로 연다 (압축·무압축·한글 파일명)', () => {
  const entries = [
    { name: 'src/app.py', data: Buffer.from('print("hi")\n'.repeat(200)) },
    { name: 'a', data: Buffer.from('x') },
    { name: '문서/설명.md', data: Buffer.from('# 한글') },
    { name: 'empty.txt', data: Buffer.alloc(0) },
  ]
  const out = execFileSync('python3', ['-I', '-c', PY], { input: buildZip(entries) })
  assert.deepEqual(JSON.parse(out.toString()), Object.fromEntries(entries.map((e) => [e.name, e.data.toString()])))
})

test('isEnvFile: 실제 .env 는 제외, 예시 파일은 포함', () => {
  for (const p of ['.env', 'api/.env.local', '.env.production']) assert.equal(isEnvFile(p), true, p)
  for (const p of ['.env.example', 'x/.env.sample', 'env.py', 'my.env.txt']) assert.equal(isEnvFile(p), false, p)
})

test('collect: 산출물·.env·심볼릭 링크를 빼고 상대 경로로 모은다', () => {
  const root = mkdtempSync(join(tmpdir(), 'sentra-'))
  mkdirSync(join(root, 'src'))
  mkdirSync(join(root, 'node_modules/pkg'), { recursive: true })
  writeFileSync(join(root, 'src/main.py'), 'x')
  writeFileSync(join(root, 'node_modules/pkg/i.js'), 'x')
  writeFileSync(join(root, '.env'), 'OPENAI_API_KEY=fake')
  writeFileSync(join(root, '.env.example'), 'OPENAI_API_KEY=')
  symlinkSync(join(root, 'src/main.py'), join(root, 'link.py'))
  const r = collect(root)
  assert.deepEqual(r.files.map((f) => f.name), ['.env.example', 'src/main.py'])
  assert.deepEqual(r.skipped.env, ['.env'])
  assert.equal(r.skipped.symlinks, 1)
  assert.deepEqual(collect(root, { includeEnv: true }).files.map((f) => f.name), ['.env', '.env.example', 'src/main.py'])
})

test('collect: target·build 같은 이름은 빌드 파일 옆일 때만 산출물로 뺀다 (Java 패키지 lab/target 은 남김)', () => {
  for (const git of [false, true]) {
    const root = mkdtempSync(join(tmpdir(), 'sentra-'))
    const files = {
      'backend/pom.xml': '<project/>',
      'backend/src/main/java/lab/target/App.java': 'class App {}',
      'backend/target/app.jar.original': 'x',
      'web/package.json': '{}',
      'web/build/index.js': 'x',
      'web/src/build/plan.ts': 'x',
      'docs/out/notes.md': 'x',
    }
    for (const [name, body] of Object.entries(files)) {
      mkdirSync(join(root, name, '..'), { recursive: true })
      writeFileSync(join(root, name), body)
    }
    if (git) execFileSync('git', ['-C', root, 'init', '-q'])
    const r = collect(root)
    assert.deepEqual(r.files.map((f) => f.name), [
      'backend/pom.xml', 'backend/src/main/java/lab/target/App.java', 'docs/out/notes.md', 'web/package.json', 'web/src/build/plan.ts',
    ], git ? 'git' : 'walk')
    if (git) assert.equal(r.skipped.generated, 2)
  }
})

// 저장소 compose 와 CLI compose 의 서비스 목록이 어긋나지 않게 (kafka-ui 는 개발용이라 제외)
const services = (text) => [...text.split(/^services:\n/m)[1].split(/^\S/m)[0].matchAll(/^ {2}([a-z][a-z-]*):\s*$/gm)].map((m) => m[1]).sort()

test('CLI compose 는 저장소 compose 와 같은 서비스를 갖고, 대시보드만 127.0.0.1 에 연다', () => {
  const cli = readFileSync(new URL('../compose.yml', import.meta.url), 'utf8')
  const repo = readFileSync(new URL('../../docker-compose.yml', import.meta.url), 'utf8')
  assert.deepEqual(services(cli), services(repo).filter((s) => s !== 'kafka-ui'))
  const ports = [...cli.matchAll(/ports: \[(.+)\]/g)].map((m) => m[1])
  assert.deepEqual(ports, ['"127.0.0.1:${SENTRA_PORT:-5180}:80"'])
  assert.doesNotMatch(cli, /build:/)
  assert.match(cli, /AUTH_MODE: local/)
})

test('writeConfig: 본인만 읽는 권한, 비밀값은 처음 한 번 만들고 유지', async () => {
  process.env.SENTRA_CONFIG_DIR = mkdtempSync(join(tmpdir(), 'sentra-cfg-'))
  const { envPath, readConfig, writeConfig } = await import('../lib/config.js')
  const a = writeConfig({ openai: 'sk-test-1', langsmith: '' }, {})
  assert.equal(statSync(envPath()).mode & 0o777, 0o600)
  assert.equal(a.JWT_SECRET.length, 64)
  const b = writeConfig({ openai: 'sk-test-2', langsmith: 'ls-1' })
  assert.equal(b.JWT_SECRET, a.JWT_SECRET)
  assert.equal(b.MCP_SERVICE_TOKEN, a.MCP_SERVICE_TOKEN)
  assert.deepEqual([readConfig().OPENAI_API_KEY, readConfig().LANGSMITH_API_KEY], ['sk-test-2', 'ls-1'])
})

test('collect: 큰 바이너리·아주 큰 파일은 빼고, 작은 바이너리(빌드 파일)는 남긴다', () => {
  const root = mkdtempSync(join(tmpdir(), 'sentra-big-'))
  mkdirSync(join(root, 'gradle/wrapper'), { recursive: true })
  writeFileSync(join(root, 'gradle/wrapper/gradle-wrapper.jar'), Buffer.alloc(60 * 1024, 1)) // 작은 jar → 유지
  writeFileSync(join(root, 'model.bin'), Buffer.alloc(MAX_BINARY_BYTES + 1, 7)) // 큰 바이너리(확장자) → 제외
  const blob = Buffer.alloc(MAX_BINARY_BYTES + 1, 65); blob[10] = 0
  writeFileSync(join(root, 'data.unknown'), blob) // 확장자 모름 + NUL 바이트 → 바이너리 → 제외
  writeFileSync(join(root, 'dump.sql'), Buffer.alloc(MAX_FILE_BYTES + 1, 65)) // 아주 큰 텍스트 → 제외
  writeFileSync(join(root, 'big.json'), Buffer.alloc(MAX_BINARY_BYTES + 1, 65)) // 5MB 미만 텍스트 → 유지
  const r = collect(root)
  assert.deepEqual(r.files.map((f) => f.name), ['big.json', 'gradle/wrapper/gradle-wrapper.jar'])
  assert.deepEqual(r.skipped.large.map((f) => f.name), ['dump.sql', 'data.unknown', 'model.bin']) // 큰 순서
})

test('ignoreMatcher: 디렉터리·확장자·경로 패턴', () => {
  const m = ignoreMatcher(['# 주석', 'data/', '*.csv', 'docs/images', 'assets/**/*.svg'])
  for (const p of ['data/a.txt', 'src/data/b.py', 'x.csv', 'a/b/c.csv', 'docs/images/1.png', 'assets/icons/x/y.svg']) assert.equal(m(p), true, p)
  for (const p of ['data', 'src/database.py', 'csv.py', 'docs/imagesx/1.png', 'src/docs/images/1.png', 'assets/a.png']) assert.equal(m(p), false, p)
})

test('collect: .sentraaiignore 와 exclude 옵션', () => {
  const root = mkdtempSync(join(tmpdir(), 'sentra-ign-'))
  mkdirSync(join(root, 'fixtures'))
  writeFileSync(join(root, 'app.py'), 'x')
  writeFileSync(join(root, 'fixtures/f.json'), 'x')
  writeFileSync(join(root, 'notes.log'), 'x')
  writeFileSync(join(root, '.sentraaiignore'), 'fixtures/\n')
  const r = collect(root, { exclude: ['*.log'] })
  assert.deepEqual(r.files.map((f) => f.name), ['.sentraaiignore', 'app.py'])
  assert.equal(r.skipped.ignored, 2)
})
