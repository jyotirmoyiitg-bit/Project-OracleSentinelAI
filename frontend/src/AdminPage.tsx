import { useEffect, useMemo, useState } from 'react'
import { ApiError, getPlatform, listAppConfig, updateAppConfig, type AppConfigItem, type PlatformSummary } from './api'

function draftFor(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value)
}

function parseDraft(current: unknown, draft: string): unknown {
  if (typeof current === 'boolean') return draft === 'true'
  if (typeof current === 'number') {
    const parsed = Number(draft)
    if (!Number.isFinite(parsed)) throw new Error('Enter a number.')
    return parsed
  }
  if (typeof current === 'string') return draft
  return JSON.parse(draft)
}

export function AdminPage({
  onOpenRegistry,
  onRefreshStatus,
}: {
  onOpenRegistry: () => void
  onRefreshStatus: (refreshing: boolean, callback: () => void) => void
}) {
  const [platform, setPlatform] = useState<PlatformSummary | null>(null)
  const [config, setConfig] = useState<AppConfigItem[] | null>(null)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [openSection, setOpenSection] = useState<string | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [saving, setSaving] = useState('')

  useEffect(() => {
    const load = () => {
      setError('')
      getPlatform().then(setPlatform).catch((reason: unknown) => {
        setError(reason instanceof ApiError ? reason.message : 'Could not load administration.')
      })
      listAppConfig().then((response) => {
        setConfig(response.items)
        setDrafts(Object.fromEntries(response.items.map((item) => [item.key, draftFor(item.value)])))
      }).catch((reason: unknown) => {
        setError(reason instanceof ApiError ? reason.message : 'Could not load configuration.')
      })
    }
    onRefreshStatus(false, load)
    load()
  }, [onRefreshStatus])

  const sections = useMemo(() => {
    const grouped = new Map<string, AppConfigItem[]>()
    for (const item of config ?? []) {
      const name = item.section || 'Other'
      grouped.set(name, [...(grouped.get(name) ?? []), item])
    }
    return [...grouped]
  }, [config])

  function resetSection(name: string) {
    const rows = (config ?? []).filter((item) => item.section === name)
    setDrafts((current) => ({ ...current, ...Object.fromEntries(rows.map((item) => [item.key, draftFor(item.value)])) }))
  }

  function beginEdit(name: string) {
    resetSection(name)
    setOpenSection(name)
    setEditing(name)
    setError('')
    setNotice('')
  }

  async function saveSection(name: string) {
    const rows = (config ?? []).filter((item) => item.section === name && item.editable)
    const changed = rows.filter((item) => (drafts[item.key] ?? '') !== draftFor(item.value))
    setSaving(name)
    setError('')
    setNotice('')
    if (changed.length === 0) {
      setEditing(null)
      setSaving('')
      setNotice(`${name} has no changes.`)
      return
    }
    try {
      for (const item of changed) {
        const value = parseDraft(item.value, drafts[item.key] ?? '')
        const saved = await updateAppConfig(item.key, value)
        setConfig((current) => current?.map((row) => row.key === item.key ? { ...row, value: saved.value, updated_at: saved.updated_at || row.updated_at } : row) ?? current)
        setDrafts((current) => ({ ...current, [item.key]: draftFor(saved.value) }))
      }
      setEditing(null)
      setNotice(`${name} saved.`)
    } catch (reason: unknown) {
      setError(reason instanceof SyntaxError ? 'Enter valid JSON for this setting.' : reason instanceof Error ? reason.message : 'Could not save that section.')
    } finally {
      setSaving('')
    }
  }

  return (
    <main className="page-shell">
      <p className="eyebrow">ADMINISTRATION</p>
      <h1>Platform</h1>
      <p>Provider keys and database passwords stay in secret references. This page does not show them and does not run proposals.</p>
      {error && <div className="inline-error" role="alert">{error}</div>}
      {notice && <p role="status">{notice}</p>}
      {platform && (
        <div className="admin-grid">
          <section>
            <h2>Registry</h2>
            <button className="button button-primary" type="button" onClick={onOpenRegistry}>Manage databases</button>
          </section>
          <section>
            <h2>Collector</h2>
            <p>Scheduler {platform.scheduler_enabled ? 'enabled' : 'disabled'}. Knowledge scans {platform.knowledge_enabled ? 'enabled' : 'disabled'}.</p>
          </section>
          <section>
            <h2>Language model</h2>
            <p>{platform.llm.provider} {platform.llm.model || '(no model name)'}. Hosted secret {platform.llm.secret_configured ? 'configured' : 'not configured'}.</p>
          </section>
          <section>
            <h2>Roles</h2>
            <p>{platform.roles.join(', ')}. Existing admin is {platform.legacy_roles.admin}. Existing auditor is {platform.legacy_roles.auditor}.</p>
          </section>
          <section>
            <h2>Rule thresholds</h2>
            <ul>{platform.rules.map((rule) => <li key={rule.id}><code>{rule.expression}</code></li>)}</ul>
          </section>
          <section>
            <h2>Notifications</h2>
            <p>SMTP {platform.notifications.smtp_enabled ? 'enabled' : 'disabled'}. Host {platform.notifications.smtp_host_configured ? 'configured' : 'not configured'}. Password secret {platform.notifications.smtp_secret_configured ? 'configured' : 'not configured'}.</p>
          </section>
          <section>
            <h2>Retention</h2>
            <ul>
              {Object.entries(platform.retention_days).map(([kind, days]) => (
                <li key={kind}>{kind}: {days === null ? 'kept until removed by intake' : `${days} days`}</li>
              ))}
            </ul>
          </section>
        </div>
      )}
      <section className="config-editor" aria-labelledby="config-title">
        <h2 id="config-title">Configuration</h2>
        <p>Sections stay closed and read-only until you edit one. Fixed settings stay visible and cannot be saved. Passwords and secrets are not listed.</p>
        {sections.map(([name, rows]) => {
          const open = openSection === name
          const canEdit = rows.some((item) => item.editable)
          const editingSection = editing === name
          return (
            <section className="config-section" key={name} aria-labelledby={`config-${name}`}>
              <div className="config-section-head">
                <h3 id={`config-${name}`}>{name}</h3>
                <div className="config-actions">
                  <button className="button" type="button" aria-expanded={open} onClick={() => { setOpenSection(open ? null : name); if (editingSection) { resetSection(name); setEditing(null) } }}>{open ? 'Hide' : 'Show'}</button>
                  {open && canEdit && !editingSection ? <button className="button" type="button" onClick={() => beginEdit(name)}>Edit</button> : null}
                  {editingSection ? <button className="button" type="button" onClick={() => { resetSection(name); setEditing(null) }}>Cancel</button> : null}
                  {editingSection ? <button className="button button-primary" type="button" disabled={saving === name} onClick={() => { void saveSection(name) }}>{saving === name ? 'Saving…' : 'Save'}</button> : null}
                </div>
              </div>
              {open ? (
                <div className="config-section-body">
                  <table className="data-table config-table">
                    <tbody>
                      {rows.map((item) => (
                        <tr key={item.key}>
                          <td><code>{item.key}</code>{item.editable ? null : <div className="config-fixed">Fixed</div>}</td>
                          <td>
                            {editingSection && item.editable ? (
                              typeof item.value === 'boolean' ? (
                                <select aria-label={item.key} value={drafts[item.key] ?? 'false'} onChange={(event) => setDrafts((current) => ({ ...current, [item.key]: event.target.value }))}>
                                  <option value="true">true</option>
                                  <option value="false">false</option>
                                </select>
                              ) : typeof item.value === 'object' && item.value !== null ? (
                                <textarea aria-label={item.key} value={drafts[item.key] ?? ''} onChange={(event) => setDrafts((current) => ({ ...current, [item.key]: event.target.value }))} />
                              ) : (
                                <input aria-label={item.key} value={drafts[item.key] ?? ''} onChange={(event) => setDrafts((current) => ({ ...current, [item.key]: event.target.value }))} />
                              )
                            ) : (
                              <span>{typeof item.value === 'string' ? item.value || '—' : JSON.stringify(item.value)}</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
            </section>
          )
        })}
      </section>
    </main>
  )
}
