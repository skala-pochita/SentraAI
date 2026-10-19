// fetch 기반 SSE: Authorization 헤더를 붙일 수 있고(EventSource 불가), 끊기면 호출자가 상태 조회 후 재구독한다.
import { tokens } from './client'

export interface SseMessage { event: string; data: string; id?: string }

export async function sse(path: string, onMessage: (m: SseMessage) => void, signal: AbortSignal): Promise<void> {
  const res = await fetch(path, { headers: { Authorization: `Bearer ${tokens.access ?? ''}`, Accept: 'text/event-stream' }, signal })
  if (!res.ok || !res.body) throw new Error(`SSE ${res.status}`)
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buf = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) return
    buf += value.replace(/\r\n/g, '\n')
    let i: number
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const raw = buf.slice(0, i); buf = buf.slice(i + 2)
      const m: SseMessage = { event: 'message', data: '' }
      for (const line of raw.split('\n')) {
        if (line.startsWith('event:')) m.event = line.slice(6).trim()
        else if (line.startsWith('data:')) m.data += line.slice(5).trim()
        else if (line.startsWith('id:')) m.id = line.slice(3).trim()
      }
      if (m.data || m.event !== 'message') onMessage(m)
    }
  }
}
