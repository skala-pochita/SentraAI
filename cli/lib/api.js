// SentraAI REST API 호출 (로컬 모드: 로그인 없음). 오류는 서버의 {"error": {code, message, details}} 를 CliError 로 바꾼다.
import { CliError } from './errors.js'

export class ApiError extends CliError {
  constructor(status, code, message, details) { super(message); this.status = status; this.code = code; this.details = details }
}

export function client(apiBase) {
  async function call(method, path, { json, form, timeout = 60_000 } = {}) {
    let res
    try {
      res = await fetch(apiBase + path, {
        method,
        // 로컬 모드 API 는 상태 변경 요청에 이 헤더를 요구한다 (backend/api/local_mode.py)
        headers: { 'X-Sentra-Local': '1', ...(json ? { 'Content-Type': 'application/json' } : {}) },
        body: json ? JSON.stringify(json) : form,
        signal: AbortSignal.timeout(timeout),
      })
    } catch (e) {
      throw new CliError(`API 호출 실패 (${method} ${path}): ${e.name === 'TimeoutError' ? '시간 초과' : e.message}`)
    }
    const body = await res.json().catch(() => ({}))
    if (!res.ok) {
      const err = body.error ?? {}
      throw new ApiError(res.status, err.code ?? String(res.status), err.message ?? res.statusText, err.details ?? {})
    }
    return body
  }
  return {
    mode: () => call('GET', '/auth/mode'),
    documents: () => call('GET', '/knowledge/documents'),
    upload(zip, filename) {
      const form = new FormData()
      form.append('file', new Blob([zip], { type: 'application/zip' }), filename)
      return call('POST', '/targets/upload', { form, timeout: 180_000 })
    },
    readiness: (tid) => call('GET', `/targets/${tid}/execution`),
    startAudit: (targetIds, mode) => call('POST', '/audits', { json: { target_ids: targetIds, mode } }),
    audit: (id) => call('GET', `/audits/${id}`),
  }
}
