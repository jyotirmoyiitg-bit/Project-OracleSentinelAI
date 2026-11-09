import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ArrowLeft, Database, Plus, RefreshCw } from 'lucide-react'
import { ApiError, getTnsAliases, registerFromTns, testTnsConnection } from './api'
import type { DatabaseRegistration, Target, TnsAliasListResponse } from './types'

export function DatabaseRegistrationPage({ onBack, onRegistered, onRefreshStatus }: {
  onBack: () => void
  onRegistered: (target: Target) => void
  onRefreshStatus: (refreshing: boolean, callback: () => void) => void
}) {
  const [aliases, setAliases] = useState<TnsAliasListResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [error, setError] = useState('')
  const [result, setResult] = useState('')
  const [retry, setRetry] = useState(0)
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState<DatabaseRegistration>({ tns_alias: '', display_name: '', environment: 'dev', owner: '' })
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setLoadError('')
    getTnsAliases(controller.signal).then((response) => {
      if (!controller.signal.aborted) setAliases(response)
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setLoadError(reason instanceof ApiError ? reason.message : 'Could not load TNS aliases.')
    }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [retry])
  useEffect(() => {
    onRefreshStatus(loading || busy, () => { if (!busy) setRetry((value) => value + 1) })
  }, [onRefreshStatus, loading, busy])

  function update(field: keyof DatabaseRegistration, value: string) {
    setForm((current) => ({
      ...current, [field]: value,
      ...(field === 'tns_alias' && (!current.display_name || current.display_name === current.tns_alias) ? { display_name: value } : {}),
    }))
    setResult('')
    setError('')
  }

  async function execute(register: boolean) {
    setBusy(true)
    setError('')
    setResult('')
    try {
      if (register) {
        const target = await registerFromTns(form)
        if (mounted.current) onRegistered(target)
      } else {
        const connection = await testTnsConnection(form)
        if (!mounted.current) return
        if (connection.status !== 'connected') setError(connection.message)
        else setResult(`Connection successful${connection.version ? ` (Oracle ${connection.version})` : ''}. Ready to register.`)
      }
    } catch (reason) {
      if (mounted.current) setError(reason instanceof ApiError ? reason.message : 'Could not complete database registration.')
    } finally {
      if (mounted.current) setBusy(false)
    }
  }

  const canSubmit = !loading && !loadError && !!form.tns_alias && !!form.display_name.trim() && !!form.environment.trim() && !busy
  const available = aliases?.items.filter((alias) => !alias.registered) ?? []
  return (
    <main className="page-shell registration-page">
      <button className="back-link" onClick={onBack}><ArrowLeft size={16} /> Fleet overview</button>
      <section className="page-heading"><div><div className="eyebrow">DATABASE REGISTRY</div><h1>Add database</h1><p>Select an alias from the backend server's tnsnames.ora.</p></div></section>
      <section className="panel registration-panel">
        <div className="panel-heading"><div><h2><Database size={18} /> Oracle database</h2><p>Registration is saved in PostgreSQL and survives application restarts.</p></div></div>
        <p className="registration-note">Only register database service aliases. Listener and external-procedure entries are not monitoring targets. Each alias is selected explicitly; nothing is imported automatically.</p>
        <p className="registration-note">Connections use the backend's configured Oracle monitoring credentials and secret references. No password is entered or stored in this browser.</p>
        {loading && <p className="registration-note" role="status">Loading TNS aliases...</p>}
        {loadError && <div className="registration-error" role="alert"><p>{loadError}</p><button className="button button-secondary" onClick={() => setRetry((value) => value + 1)}><RefreshCw size={14} /> Retry aliases</button></div>}
        {!loading && !loadError && aliases && available.length === 0 && <p className="registration-note">No unregistered aliases are available. Update the backend tnsnames.ora or choose an existing database in the sidebar.</p>}
        {aliases && !aliases.scheduler_enabled && <p className="registration-warning" role="status">Automatic monitoring is disabled. Enable SCHEDULER_ENABLED=true in the backend .env and restart it to collect data.</p>}
        <form className="registration-form" onSubmit={(event: FormEvent) => { event.preventDefault(); if (canSubmit) void execute(true) }}>
          <fieldset disabled={busy || loading || !!loadError}>
            <label>TNS alias<select required aria-label="TNS alias" value={form.tns_alias} onChange={(event) => update('tns_alias', event.target.value)}>
              <option value="">Select a database alias</option>
              {aliases?.items.map((alias) => <option value={alias.name} key={alias.name} disabled={alias.registered}>{alias.name}{alias.registered ? ' (already registered)' : ''}</option>)}
            </select></label>
            <label>Display name<input required maxLength={255} value={form.display_name} onChange={(event) => update('display_name', event.target.value)} /></label>
            <label>Environment<select value={form.environment} onChange={(event) => update('environment', event.target.value)}><option value="dev">Development</option><option value="staging">Staging</option><option value="production">Production</option></select></label>
            <label>Owner (optional)<input maxLength={255} value={form.owner} onChange={(event) => update('owner', event.target.value)} /></label>
          </fieldset>
          {error && <div className="registration-error" role="alert">{error}</div>}
          {result && <p className="registration-success" role="status">{result}</p>}
          <div className="registration-actions">
            <button type="button" className="button button-secondary" disabled={!canSubmit} onClick={() => { void execute(false) }}>Test connection</button>
            <button type="submit" className="button button-primary" disabled={!canSubmit}><Plus size={15} />{busy ? 'Working...' : 'Register database'}</button>
          </div>
          <p className="registration-note">Registration rechecks the connection before saving. A connection test does not mark database health green; health stays unknown until fresh monitoring probes complete.</p>
        </form>
      </section>
    </main>
  )
}
