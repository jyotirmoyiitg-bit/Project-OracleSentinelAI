import { Fragment, useState } from 'react'
import { ChevronDown, ChevronRight, Monitor, User } from 'lucide-react'
import { getDatabaseSessions, type SessionList, type SessionRow } from './api'
import { DatabaseView } from './components/DatabaseView'
import { SessionStatementPanel, type ChosenSession } from './components/SessionStatementPanel'

const STATUSES = [
  ['all', 'All sessions'],
  ['active', 'Active only'],
  ['inactive', 'Inactive only'],
] as const
type StatusFilter = (typeof STATUSES)[number][0]

const HIDDEN = 'Shown to an administrator'

/** Seconds since the last call, as a short phrase. */
export function idleText(seconds: number | null): string {
  if (seconds == null) return ''
  if (seconds < 60) return `${seconds}s since the last call`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ${seconds % 60}s since the last call`
  const hours = Math.floor(minutes / 60)
  if (hours < 48) return `${hours}h ${minutes % 60}m since the last call`
  return `${Math.floor(hours / 24)}d ${hours % 24}h since the last call`
}

function Restricted({ value, restricted }: { value: string; restricted: boolean }) {
  if (restricted) return <span className="section-muted" title={HIDDEN}>—</span>
  return <>{value || '—'}</>
}

function matches(row: SessionRow, program: string, wanted: string): boolean {
  if (!wanted) return true
  return [program, row.username, row.os_user, row.terminal, row.module, row.action, row.sql_id, String(row.sid ?? '')]
    .join(' ').toLowerCase().includes(wanted)
}

function SessionTree({ picture, databaseId }: { picture: SessionList; databaseId: string }) {
  const [chosen, setChosen] = useState<ChosenSession | null>(null)
  const [filter, setFilter] = useState('')
  const [status, setStatus] = useState<StatusFilter>('all')
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const wanted = filter.trim().toLowerCase()
  const groups = picture.groups
    .map((group) => ({
      ...group,
      shown: group.sessions.filter((row) => (
        (status === 'all' || (status === 'active') === (row.status === 'ACTIVE')) && matches(row, group.program, wanted)
      )),
    }))
    .filter((group) => group.shown.length > 0)
  const shown = groups.reduce((sum, group) => sum + group.shown.length, 0)
  const allCollapsed = groups.length > 0 && groups.every((group) => collapsed.has(group.program))

  const toggle = (program: string) => setCollapsed((current) => {
    const next = new Set(current)
    if (next.has(program)) next.delete(program)
    else next.add(program)
    return next
  })

  const choose = (row: SessionRow, program: string) => {
    if (row.sid == null || row.serial == null) return
    setChosen({ sid: row.sid, serial: row.serial, program, status: row.status })
  }
  const isChosen = (row: SessionRow) => chosen != null && row.sid === chosen.sid && row.serial === chosen.serial

  return (
    <>
    <section className="panel" aria-labelledby="session-browser">
      <div className="panel-heading section-heading-wrap">
        <div>
          <h2 id="session-browser">Sessions by program</h2>
          <p>{picture.caption}</p>
        </div>
        <div className="section-controls">
          <span className="section-figure"><small>Sessions</small>{picture.total}</span>
          <span className="section-figure"><small>Active</small><span className={picture.active > 0 ? 'session-count-active' : undefined}>{picture.active}</span></span>
          <span className="section-figure"><small>Programs</small>{picture.programs}</span>
          <label className="filter-control">
            <span className="sr-only">Sessions shown</span>
            <select aria-label="Sessions shown" value={status} onChange={(event) => setStatus(event.target.value as StatusFilter)}>
              {STATUSES.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            </select>
          </label>
          <label className="section-search">
            <span className="sr-only">Filter sessions</span>
            <input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Filter by program, user, module, SID, or SQL_ID" aria-label="Filter sessions" />
          </label>
          <button
            type="button"
            className="button button-secondary"
            onClick={() => setCollapsed(allCollapsed ? new Set() : new Set(groups.map((group) => group.program)))}
            disabled={groups.length === 0}
          >
            {allCollapsed ? 'Expand all' : 'Collapse all'}
          </button>
        </div>
      </div>
      <p className="wait-caption section-caption">
        Showing {shown} of {picture.total} sessions in {groups.length} programs.
        {picture.capped ? ' The reading stops at 500 sessions, so some may be missing.' : ''}
      </p>
      <div className="table-scroll section-table-scroll section-table-tall">
        <table className="data-table section-table session-table">
          <thead>
            <tr>
              <th scope="col">Program / user name</th>
              <th scope="col">OS user</th>
              <th scope="col">Terminal</th>
              <th scope="col" className="section-number">SID</th>
              <th scope="col">Status</th>
              <th scope="col">Module</th>
              <th scope="col">Action</th>
              <th scope="col">Logon time</th>
              <th scope="col">Last call</th>
              <th scope="col">SQL_ID</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((group) => {
              const open = !collapsed.has(group.program)
              return (
                <Fragment key={group.program}>
                  <tr className="session-group">
                    <th scope="rowgroup" colSpan={10}>
                      <button
                        type="button"
                        className="session-group-toggle"
                        aria-expanded={open}
                        aria-label={`${group.program}, ${group.shown.length} sessions`}
                        onClick={() => toggle(group.program)}
                      >
                        {open ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
                        <Monitor size={14} aria-hidden="true" />
                        <strong>{group.program}</strong>
                        <span className="session-group-count">({group.shown.length})</span>
                        {group.active > 0 && <span className="section-state section-state-good">{group.active} active</span>}
                      </button>
                    </th>
                  </tr>
                  {open && group.shown.map((row) => (
                    <tr
                      key={`${group.program}:${row.sid}:${row.serial}`}
                      className={['session-row', row.status === 'ACTIVE' ? 'session-active' : '', isChosen(row) ? 'session-chosen' : ''].filter(Boolean).join(' ')}
                      aria-selected={isChosen(row)}
                      tabIndex={0}
                      onClick={() => choose(row, group.program)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' || event.key === ' ') {
                          event.preventDefault()
                          choose(row, group.program)
                        }
                      }}
                    >
                      <td className="session-user">
                        <span className="session-user-cell">
                          <User size={13} aria-hidden="true" />
                          <Restricted value={row.username} restricted={row.restricted} />
                        </span>
                      </td>
                      <td><Restricted value={row.os_user} restricted={row.restricted} /></td>
                      <td><Restricted value={row.terminal} restricted={row.restricted} /></td>
                      <td className="section-number">{row.sid ?? '—'}</td>
                      <td>{row.status || '—'}</td>
                      <td>{row.module || '—'}</td>
                      <td className="session-action">{row.action || ''}</td>
                      <td>{row.logon_time || '—'}</td>
                      <td title={idleText(row.idle_seconds)}>{row.last_call || '—'}</td>
                      <td className="session-sql">{row.sql_id}</td>
                    </tr>
                  ))}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
      {groups.length === 0 && <p className="empty-inline">{picture.total === 0 ? 'No user session is connected.' : 'No session matches.'}</p>}
    </section>
    <SessionStatementPanel databaseId={databaseId} chosen={chosen} detail={picture.detail} refreshKey={picture.collected_at} />
    </>
  )
}

/** SessionDB: the user sessions of one database, grouped by program like a session browser. Nothing can be changed. */
export function SessionPage({ databaseId, onAskCopilot, onOpenDatabase, onRefreshStatus }: {
  databaseId: string
  onAskCopilot: (id: string) => void
  onOpenDatabase: (id: string) => void
  onRefreshStatus: (refreshing: boolean, callback: () => void) => void
}) {
  return (
    <DatabaseView
      databaseId={databaseId}
      eyebrow="SessionDB"
      lead="Who is connected and what each session is doing, grouped by the program that opened it. Choose a session to see its statement and cached plan. This page does not end or change a session."
      load={getDatabaseSessions}
      onAskCopilot={onAskCopilot}
      onOpenDatabase={onOpenDatabase}
      onRefreshStatus={onRefreshStatus}
    >
      {(picture, target) => <SessionTree picture={picture} databaseId={target.id} />}
    </DatabaseView>
  )
}
