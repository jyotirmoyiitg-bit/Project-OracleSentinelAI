import { useEffect, useRef, useState } from 'react'
import { getDatabaseAlertLog, type AlertTail } from './api'
import { DatabaseView } from './components/DatabaseView'

const LEVELS = [
  ['all', 'All lines'],
  ['attention', 'Warnings and errors'],
  ['error', 'Errors only'],
] as const
type Level = (typeof LEVELS)[number][0]

function AlertLines({ picture }: { picture: AlertTail }) {
  const [filter, setFilter] = useState('')
  const [level, setLevel] = useState<Level>('all')
  const scroller = useRef<HTMLDivElement>(null)
  const wanted = filter.trim().toLowerCase()
  const lines = picture.lines.filter((line) => (
    (level === 'all' || line.severity === 'error' || (level === 'attention' && line.severity === 'warning'))
    && (!wanted || `${line.at} ${line.text}`.toLowerCase().includes(wanted))
  ))

  // The log reads oldest first, like the file, so the newest line is at the bottom.
  useEffect(() => {
    const element = scroller.current
    if (element) element.scrollTop = element.scrollHeight
  }, [picture.collected_at, level, wanted])

  return (
    <section className="panel" aria-labelledby="alert-log">
      <div className="panel-heading section-heading-wrap">
        <div>
          <h2 id="alert-log">Alert log</h2>
          <p>{picture.caption} {picture.window}</p>
        </div>
        <div className="section-controls">
          <span className="section-figure"><small>Errors</small><span className={picture.errors > 0 ? 'section-count-bad' : undefined}>{picture.errors}</span></span>
          <span className="section-figure"><small>Warnings</small><span className={picture.warnings > 0 ? 'section-count-warn' : undefined}>{picture.warnings}</span></span>
          <label className="filter-control">
            <span className="sr-only">Lines shown</span>
            <select aria-label="Lines shown" value={level} onChange={(event) => setLevel(event.target.value as Level)}>
              {LEVELS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            </select>
          </label>
          <label className="section-search">
            <span className="sr-only">Filter alert log</span>
            <input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Filter by text, such as ORA-00600" aria-label="Filter alert log" />
          </label>
        </div>
      </div>
      <p className="wait-caption section-caption">Showing {lines.length} of {picture.lines.length} lines. The newest line is last.</p>
      <div className="alert-log" ref={scroller} role="log" aria-label="Alert log lines" tabIndex={0}>
        {lines.length === 0 ? <p className="empty-inline">No line matches.</p> : (
          <ol>
            {lines.map((line, index) => (
              <li key={`${line.at}:${index}`} className={`alert-line alert-line-${line.severity}`}>
                <time>{line.at}</time>
                <span>{line.text}</span>
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  )
}

/** AlertDB: the most recent lines of the database alert log, with errors and warnings marked. */
export function AlertPage({ databaseId, onAskCopilot, onOpenDatabase, onRefreshStatus }: {
  databaseId: string
  onAskCopilot: (id: string) => void
  onOpenDatabase: (id: string) => void
  onRefreshStatus: (refreshing: boolean, callback: () => void) => void
}) {
  return (
    <DatabaseView
      databaseId={databaseId}
      eyebrow="AlertDB"
      lead="What the database has written to its alert log: log switches, resize operations, errors, and warnings. This page does not change Oracle."
      load={getDatabaseAlertLog}
      onAskCopilot={onAskCopilot}
      onOpenDatabase={onOpenDatabase}
      onRefreshStatus={onRefreshStatus}
    >
      {(picture) => <AlertLines picture={picture} />}
    </DatabaseView>
  )
}
