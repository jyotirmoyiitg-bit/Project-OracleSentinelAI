import { Fragment, type ReactNode } from 'react'

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\n]+\*)/g

function inline(text: string): ReactNode[] {
  return text.split(INLINE).map((part, index) => {
    if (part.startsWith('**') && part.endsWith('**')) return <strong key={index}>{part.slice(2, -2)}</strong>
    if (part.startsWith('`') && part.endsWith('`')) return <code key={index}>{part.slice(1, -1)}</code>
    if (part.startsWith('*') && part.endsWith('*') && part.length > 2) return <em key={index}>{part.slice(1, -1)}</em>
    return <Fragment key={index}>{part}</Fragment>
  })
}

function isTableSeparator(line: string): boolean {
  return /^\|?[\s:|-]+\|?$/.test(line) && line.includes('-')
}

function cells(line: string): string[] {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim())
}

/** Small safe Markdown subset for model summaries. Builds React nodes only; no raw HTML. */
export function Markdown({ text }: { text: string }) {
  const lines = text.split('\n')
  const blocks: ReactNode[] = []
  let index = 0
  let key = 0
  while (index < lines.length) {
    const line = lines[index]
    if (!line.trim()) { index += 1; continue }
    if (line.trim().startsWith('```')) {
      const code: string[] = []
      index += 1
      while (index < lines.length && !lines[index].trim().startsWith('```')) { code.push(lines[index]); index += 1 }
      index += 1
      blocks.push(<pre key={key++} className="copilot-code"><code>{code.join('\n')}</code></pre>)
      continue
    }
    const heading = /^(#{1,3})\s+(.*)$/.exec(line)
    if (heading) {
      const body = inline(heading[2])
      blocks.push(heading[1].length === 1 ? <h3 key={key++}>{body}</h3> : <h4 key={key++}>{body}</h4>)
      index += 1
      continue
    }
    if (line.trim().startsWith('|') && index + 1 < lines.length && isTableSeparator(lines[index + 1])) {
      const head = cells(line)
      index += 2
      const rows: string[][] = []
      while (index < lines.length && lines[index].trim().startsWith('|')) { rows.push(cells(lines[index])); index += 1 }
      blocks.push(
        <div key={key++} className="copilot-table-wrap"><table className="copilot-table">
          <thead><tr>{head.map((cell, cellIndex) => <th key={cellIndex}>{inline(cell)}</th>)}</tr></thead>
          <tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{inline(cell)}</td>)}</tr>)}</tbody>
        </table></div>,
      )
      continue
    }
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = []
      while (index < lines.length && /^\s*[-*]\s+/.test(lines[index])) { items.push(lines[index].replace(/^\s*[-*]\s+/, '')); index += 1 }
      blocks.push(<ul key={key++}>{items.map((item, itemIndex) => <li key={itemIndex}>{inline(item)}</li>)}</ul>)
      continue
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: string[] = []
      while (index < lines.length && /^\s*\d+[.)]\s+/.test(lines[index])) { items.push(lines[index].replace(/^\s*\d+[.)]\s+/, '')); index += 1 }
      blocks.push(<ol key={key++}>{items.map((item, itemIndex) => <li key={itemIndex}>{inline(item)}</li>)}</ol>)
      continue
    }
    const paragraph: string[] = []
    while (index < lines.length && lines[index].trim()) { paragraph.push(lines[index]); index += 1 }
    blocks.push(<p key={key++}>{inline(paragraph.join(' '))}</p>)
  }
  return <div className="copilot-markdown">{blocks}</div>
}
