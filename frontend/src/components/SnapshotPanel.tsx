import { useCallback, useEffect, useState } from 'react'
import { Camera, CircleAlert, Settings2 } from 'lucide-react'
import {
  ApiError, collectSnapshot, getDatabaseAgentSettings, getDatabaseSnapshots, setDatabaseAgentSetting,
  type AgentSettingItem, type DatabaseAgentSettings, type DatabaseSnapshots,
} from '../api'
import { formatDate, relativeTime } from '../format'

const PACK_CHOICES = [
  { name: 'core', label: 'Core' },
  { name: 'performance', label: 'Performance' },
  { name: 'configuration', label: 'Configuration and security' },
]

function shown(value: boolean | number | string | null): string {
  if (typeof value === 'boolean') return value ? 'On' : 'Off'
  if (value === null || value === '') return 'Not set'
  return String(value).split(',').join(', ')
}

function SettingRow({
  item,
  editable,
  busy,
  onChange,
}: {
  item: AgentSettingItem
  editable: boolean
  busy: boolean
  onChange: (key: string, value: boolean | number | string | null) => void
}) {
  const [draft, setDraft] = useState(String(item.value))
  useEffect(() => { setDraft(String(item.value)) }, [item.value])
  const source = item.overridden ? 'This database' : 'System default'
  let control = null
  if (editable && typeof item.system_value === 'boolean') {
    control = (
      <select
        aria-label={item.label}
        disabled={busy}
        value={item.overridden ? String(item.database_value) : 'default'}
        onChange={(event) => onChange(item.key, event.target.value === 'default' ? null : event.target.value === 'true')}
      >
        <option value="default">System default ({shown(item.system_value)})</option>
        <option value="true">On</option>
        <option value="false">Off</option>
      </select>
    )
  } else if (editable && typeof item.system_value === 'number') {
    control = (
      <span className="agent-setting-edit">
        <input aria-label={item.label} type="number" value={draft} disabled={busy} onChange={(event) => setDraft(event.target.value)} />
        <button className="button button-secondary" type="button" disabled={busy || draft.trim() === ''} onClick={() => onChange(item.key, Number(draft))}>Save</button>
        {item.overridden && <button className="button button-secondary" type="button" disabled={busy} onClick={() => onChange(item.key, null)}>Use default</button>}
      </span>
    )
  } else if (editable) {
    const chosen = draft.split(',').map((name) => name.trim()).filter(Boolean)
    control = (
      <span className="agent-setting-edit">
        {PACK_CHOICES.map((pack) => (
          <label key={pack.name}>
            <input
              type="checkbox"
              disabled={busy}
              checked={chosen.includes(pack.name)}
              onChange={(event) => {
                const next = PACK_CHOICES.map((choice) => choice.name)
                  .filter((name) => (name === pack.name ? event.target.checked : chosen.includes(name)))
                setDraft(next.join(','))
              }}
            /> {pack.label}
          </label>
        ))}
        <button className="button button-secondary" type="button" disabled={busy || chosen.length === 0} onClick={() => onChange(item.key, draft)}>Save</button>
        {item.overridden && <button className="button button-secondary" type="button" disabled={busy} onClick={() => onChange(item.key, null)}>Use default</button>}
      </span>
    )
  }
  return (
    <tr>
      <td>{item.label}</td>
      <td>{shown(item.value)}</td>
      <td>{source}</td>
      {editable && <td>{control}</td>}
    </tr>
  )
}

function DatabaseAgentSettingsTable({ targetId, onChanged }: { targetId: string; onChanged: () => void }) {
  const [settings, setSettings] = useState<DatabaseAgentSettings | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    getDatabaseAgentSettings(targetId, controller.signal).then((result) => {
      if (result && Array.isArray(result.items)) setSettings(result)
    }).catch((reason: unknown) => {
      if (reason instanceof ApiError) setError(reason.message)
    })
    return () => controller.abort()
  }, [targetId])

  async function change(key: string, value: boolean | number | string | null) {
    setBusy(true)
    setError('')
    try {
      setSettings(await setDatabaseAgentSetting(targetId, key, value))
      onChanged()
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : 'Could not save the setting.')
    } finally {
      setBusy(false)
    }
  }

  if (!settings) return error ? <div className="inline-error agent-inline" role="alert">{error}</div> : null
  return (
    <div className="agent-settings">
      {error && <div className="inline-error agent-inline" role="alert"><CircleAlert size={16} /> {error}</div>}
      {!settings.system_enabled && <p className="agent-note">Agents are turned off for the whole system in Admin, Agents. These values apply once they are turned on.</p>}
      <table className="data-table agent-settings-table">
        <thead><tr><th>Setting</th><th>Applies</th><th>From</th>{settings.editable && <th>Change for this database</th>}</tr></thead>
        <tbody>
          {settings.items.map((item) => (
            <SettingRow key={item.key} item={item} editable={settings.editable} busy={busy} onChange={(key, value) => { void change(key, value) }} />
          ))}
        </tbody>
      </table>
      {!settings.editable && <p className="agent-note">An administrator can change these values for this database.</p>}
    </div>
  )
}

/**
 * Snapshot of one database: when it was last collected, what changed since the one before, and
 * this database's agent settings. Renders nothing when the agent routes cannot be reached.
 */
export function SnapshotPanel({ targetId }: { targetId: string }) {
  const [data, setData] = useState<DatabaseSnapshots | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const load = useCallback((signal?: AbortSignal) => {
    getDatabaseSnapshots(targetId, signal).then((result) => {
      // An API without the agent routes may answer with something else. Show the panel only for a real answer.
      if (result && Array.isArray(result.readings)) setData(result)
    }).catch(() => undefined)
  }, [targetId])

  useEffect(() => {
    const controller = new AbortController()
    load(controller.signal)
    return () => controller.abort()
  }, [load])

  async function collect() {
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const result = await collectSnapshot(targetId)
      setMessage(`Snapshot stored: ${result.answered} of ${result.readings.length} readings answered.`)
      load()
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : 'Could not collect the snapshot.')
    } finally {
      setBusy(false)
    }
  }

  if (!data) return null
  const changed = data.readings.filter((item) => item.changes.length > 0)
  const compared = data.readings.some((item) => item.previous_at)
  const unread = data.readings.filter((item) => item.status !== 'ok')
  const hidden = data.readings.filter((item) => item.hidden).length
  return (
    <section className="panel agent-panel" aria-labelledby="snapshot-title">
      <div className="panel-heading">
        <div>
          <h2 id="snapshot-title"><Camera size={18} /> Snapshot</h2>
          <p>
            {data.last_taken_at
              ? `Last collected ${relativeTime(data.last_taken_at)} (${formatDate(data.last_taken_at)}). ${data.snapshot_count} stored, kept for ${data.retention_days} days.`
              : 'No snapshot has been collected for this database.'}
          </p>
        </div>
        {data.can_collect && (
          <button className="button button-primary" type="button" disabled={busy} onClick={() => { void collect() }}>
            <Camera size={14} /> {busy ? 'Collecting…' : 'Collect snapshot'}
          </button>
        )}
      </div>
      {error && <div className="inline-error agent-inline" role="alert"><CircleAlert size={16} /> {error}</div>}
      {message && <div className="inline-success agent-inline" role="status">{message}</div>}
      {!data.enabled && <p className="empty-inline">Agents are turned off for this database, so no snapshot is collected.</p>}
      {data.readings.length > 0 && (
        <div className="agent-panel-body">
          <h3 className="agent-subhead">What changed since the previous snapshot</h3>
          {changed.length > 0 ? (
            <ul className="agent-changes" aria-label="What changed">
              {changed.map((item) => (
                <li key={item.kind}>
                  <strong>{item.label}</strong>
                  <ul>{item.changes.map((line) => <li key={line}>{line}</li>)}</ul>
                </li>
              ))}
            </ul>
          ) : (
            <p className="agent-note">
              {compared ? 'Nothing changed since the previous snapshot.' : 'This is the first snapshot. Collect another later to see what changed.'}
            </p>
          )}
          <p className="agent-note">
            {data.readings.length} readings stored: {data.readings.map((item) => item.label).join(', ')}.
            {unread.length > 0 && ` Not read: ${unread.map((item) => item.label).join(', ')}.`}
            {hidden > 0 && ` ${hidden} hold SQL text, user names, or configuration and are shown to an administrator only.`}
          </p>
        </div>
      )}
      <details className="agent-settings-box">
        <summary><Settings2 size={14} /> Agent settings for this database</summary>
        <DatabaseAgentSettingsTable targetId={targetId} onChanged={() => load()} />
      </details>
    </section>
  )
}
