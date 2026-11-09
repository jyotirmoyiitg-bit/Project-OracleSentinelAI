import { useMemo, useState } from 'react'
import { ArrowDown, ArrowUp, ArrowUpDown } from 'lucide-react'
import type { TargetDetails } from '../types'
import { formatBytes, formatDate } from '../format'

type SortKey = 'name' | 'used_pct' | 'size_bytes' | 'autoextend' | 'timestamp'

export function TablespaceTable({ tablespaces }: { tablespaces: TargetDetails['tablespaces'] }) {
  const [sortKey, setSortKey] = useState<SortKey>('used_pct')
  const [descending, setDescending] = useState(true)
  const sorted = useMemo(() => [...tablespaces].sort((a, b) => {
    const first = a[sortKey]
    const second = b[sortKey]
    let result: number
    if (first == null || second == null) result = first == null ? 1 : -1
    else if (typeof first === 'number' && typeof second === 'number') result = first - second
    else result = String(first).localeCompare(String(second))
    return descending ? -result : result
  }), [tablespaces, sortKey, descending])

  function sortBy(key: SortKey) {
    if (sortKey === key) setDescending(!descending)
    else {
      setSortKey(key)
      setDescending(key !== 'name')
    }
  }

  function heading(label: string, key: SortKey) {
    const Icon = sortKey === key ? descending ? ArrowDown : ArrowUp : ArrowUpDown
    return (
      <button className="sort-button" onClick={() => sortBy(key)} aria-label={`Sort by ${label}`}>
        {label}<Icon size={13} aria-hidden="true" />
      </button>
    )
  }

  return (
    <div className="table-scroll">
      <table className="data-table tablespace-table">
        <thead>
          <tr>
            <th scope="col">{heading('Tablespace', 'name')}</th>
            <th scope="col">{heading('Used', 'used_pct')}</th>
            <th scope="col">{heading('Size', 'size_bytes')}</th>
            <th scope="col">{heading('Autoextend', 'autoextend')}</th>
            <th scope="col">{heading('Collected', 'timestamp')}</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((space) => (
            <tr key={`${space.name}-${space.timestamp}`}>
              <td className="primary-cell">{space.name}</td>
              <td>
                {space.used_pct == null ? <span className="muted">Unavailable</span> : (
                  <div className="usage-cell">
                    <span className="usage-track" aria-label={`${space.name} usage ${space.used_pct}%`}>
                      <span className={`usage-fill ${space.used_pct >= 90 ? 'usage-high' : space.used_pct >= 75 ? 'usage-mid' : ''}`} style={{ width: `${Math.min(100, Math.max(0, space.used_pct))}%` }} />
                    </span>
                    <strong>{space.used_pct.toFixed(1)}%</strong>
                  </div>
                )}
              </td>
              <td>{formatBytes(space.size_bytes)}</td>
              <td>{space.autoextend == null ? 'Unavailable' : space.autoextend ? 'Enabled' : 'Disabled'}</td>
              <td className="muted">{formatDate(space.timestamp)}</td>
            </tr>
          ))}
          {sorted.length === 0 && (
            <tr><td className="table-empty" colSpan={5}>No tablespace measurements are available.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
