/**
 * A light SQL layout for reading: each main clause starts a line and AND/OR conditions are indented.
 * Text inside quotes and comments is never changed, and no keyword's case is changed.
 */
const CLAUSES = [
  'UNION ALL', 'UNION', 'INTERSECT', 'MINUS',
  'SELECT', 'INSERT INTO', 'UPDATE', 'DELETE FROM', 'MERGE INTO', 'WITH',
  'FROM', 'WHERE', 'GROUP BY', 'HAVING', 'ORDER BY', 'CONNECT BY', 'START WITH',
  'SET', 'VALUES', 'RETURNING', 'FETCH FIRST', 'OFFSET',
  'LEFT OUTER JOIN', 'RIGHT OUTER JOIN', 'FULL OUTER JOIN', 'LEFT JOIN', 'RIGHT JOIN', 'FULL JOIN',
  'INNER JOIN', 'CROSS JOIN', 'JOIN', 'WHEN MATCHED', 'WHEN NOT MATCHED',
]
const CONDITIONS = ['AND', 'OR']
const INDENT = '    '

type Piece = { text: string; code: boolean }

/** Split into code and the parts that must stay untouched: quoted strings, quoted names, and comments. */
function pieces(sql: string): Piece[] {
  const out: Piece[] = []
  let code = ''
  let index = 0
  const flush = () => { if (code) out.push({ text: code, code: true }); code = '' }
  while (index < sql.length) {
    const rest = sql.slice(index)
    let end = -1
    if (rest.startsWith("'")) {
      end = 1
      while (end < rest.length) {
        if (rest[end] === "'" && rest[end + 1] === "'") end += 2
        else if (rest[end] === "'") { end += 1; break }
        else end += 1
      }
    } else if (rest.startsWith('"')) {
      const close = rest.indexOf('"', 1)
      end = close < 0 ? rest.length : close + 1
    } else if (rest.startsWith('--')) {
      const close = rest.indexOf('\n')
      end = close < 0 ? rest.length : close
    } else if (rest.startsWith('/*')) {
      const close = rest.indexOf('*/', 2)
      end = close < 0 ? rest.length : close + 2
    }
    if (end > 0) {
      flush()
      out.push({ text: rest.slice(0, end), code: false })
      index += end
    } else {
      code += sql[index]
      index += 1
    }
  }
  flush()
  return out
}

function escape(word: string): string {
  return word.replace(/\s+/g, '\\s+')
}

const CLAUSE_PATTERN = new RegExp(`\\b(${CLAUSES.map(escape).join('|')})\\b`, 'gi')
const CONDITION_PATTERN = new RegExp(`\\b(${CONDITIONS.join('|')})\\b`, 'gi')

export function formatSql(sql: string): string {
  if (!sql.trim()) return sql
  // Statements that already carry their own line breaks are left as written.
  if (/\n\s*\S/.test(sql.trim())) return sql.trim()
  let depth = 0
  const formatted = pieces(sql.replace(/\s+$/, '')).map((piece) => {
    if (!piece.code) return piece.text
    // Each stretch between brackets is laid out at the depth of the brackets around it.
    return piece.text.replace(/\s+/g, ' ').split(/([()])/).map((part) => {
      if (part === '(') { depth += 1; return part }
      if (part === ')') { depth = Math.max(0, depth - 1); return part }
      return part
        .replace(CLAUSE_PATTERN, (match) => `\n${INDENT.repeat(depth)}${match.replace(/\s+/g, ' ')}`)
        .replace(CONDITION_PATTERN, (match) => `\n${INDENT.repeat(depth + 1)}${match}`)
    }).join('')
  }).join('')
  return formatted.split('\n').map((line) => line.replace(/\s+$/, '')).filter((line, index) => line.trim() || index > 0).join('\n').trim()
}
