// 보고서용 최소 Markdown 렌더러. 감사 대상 텍스트가 섞여 있으므로 먼저 전부 HTML 이스케이프한 뒤
// 제한된 문법(제목·목록·표·코드·굵게·인용·구분선)만 태그로 바꾼다. 링크·이미지·원시 HTML 은 지원하지 않는다.
const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

const inline = (s: string) =>
  esc(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')

export function renderMarkdown(md: string): string {
  const lines = md.replace(/\r\n/g, '\n').split('\n')
  const out: string[] = []
  let i = 0
  while (i < lines.length) {
    const l = lines[i]
    if (l.startsWith('```')) {
      const buf: string[] = []
      i++
      while (i < lines.length && !lines[i].startsWith('```')) buf.push(lines[i++])
      i++
      out.push(`<pre><code>${esc(buf.join('\n'))}</code></pre>`)
      continue
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(l)
    if (h) { out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); i++; continue }
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(l)) { out.push('<hr />'); i++; continue }
    if (l.trim().startsWith('|')) {
      const rows: string[][] = []
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        const cells = lines[i].trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim())
        if (!cells.every((c) => /^:?-{2,}:?$/.test(c))) rows.push(cells)
        i++
      }
      const [head, ...body] = rows
      out.push('<div class="md-table"><table><thead><tr>' + (head ?? []).map((c) => `<th>${inline(c)}</th>`).join('') + '</tr></thead><tbody>'
        + body.map((r) => '<tr>' + r.map((c) => `<td>${inline(c)}</td>`).join('') + '</tr>').join('') + '</tbody></table></div>')
      continue
    }
    if (/^\s*[-*]\s+/.test(l)) {
      const items: string[] = []
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*[-*]\s+/, ''))
      out.push('<ul>' + items.map((t) => `<li>${inline(t)}</li>`).join('') + '</ul>')
      continue
    }
    if (l.startsWith('>')) {
      const buf: string[] = []
      while (i < lines.length && lines[i].startsWith('>')) buf.push(lines[i++].replace(/^>\s?/, ''))
      out.push(`<blockquote>${buf.map(inline).join('<br />')}</blockquote>`)
      continue
    }
    if (!l.trim()) { i++; continue }
    const buf: string[] = []
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|\||\s*[-*]\s|>)/.test(lines[i])) buf.push(lines[i++])
    if (!buf.length) { out.push(`<p>${inline(l)}</p>`); i++; continue }
    out.push(`<p>${buf.map(inline).join('<br />')}</p>`)
  }
  return out.join('\n')
}
