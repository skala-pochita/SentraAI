// REST 클라이언트: access 토큰 첨부, 401 시 refresh 회전 후 1회 재시도
const BASE = '/api/v1'

export interface ApiErrorBody { code: string; message: string; details?: unknown }
export class ApiError extends Error {
  constructor(public status: number, public body: ApiErrorBody) { super(body.message) }
}

const tokens = {
  get access() { return localStorage.getItem('access_token') },
  get refresh() { return localStorage.getItem('refresh_token') },
  set(access: string, refresh: string) {
    localStorage.setItem('access_token', access)
    localStorage.setItem('refresh_token', refresh)
  },
  clear() {
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
  },
}
export { tokens }

async function tryRefresh(): Promise<boolean> {
  const rt = tokens.refresh
  if (!rt) return false
  const res = await fetch(`${BASE}/auth/refresh`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: rt }),
  })
  if (!res.ok) { tokens.clear(); return false }
  const data = await res.json()
  tokens.set(data.access_token, data.refresh_token)
  return true
}

export async function api<T = unknown>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  if (tokens.access) headers.set('Authorization', `Bearer ${tokens.access}`)
  const res = await fetch(`${BASE}${path}`, { ...init, headers })
  if (res.status === 401 && retry && (await tryRefresh())) return api<T>(path, init, false)
  if (!res.ok) {
    let body: ApiErrorBody = { code: 'HTTP_' + res.status, message: res.statusText }
    try { body = (await res.json()).error ?? body } catch { /* 본문 없음 */ }
    throw new ApiError(res.status, body)
  }
  if (res.status === 204) return undefined as T
  const ct = res.headers.get('content-type') ?? ''
  return (ct.includes('json') ? res.json() : res.text()) as Promise<T>
}

export const get = <T>(p: string) => api<T>(p)
export const post = <T>(p: string, body?: unknown) => api<T>(p, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
export const patch = <T>(p: string, body: unknown) => api<T>(p, { method: 'PATCH', body: JSON.stringify(body) })
export const put = <T>(p: string, body: unknown) => api<T>(p, { method: 'PUT', body: JSON.stringify(body) })
export const del = <T>(p: string) => api<T>(p, { method: 'DELETE' })
