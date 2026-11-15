import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { KeyRound, UserPlus } from 'lucide-react'
import {
  ApiError,
  createOperator,
  listOperators,
  resetOperatorPassword,
  setOperatorEnabled,
  setOperatorRole,
  type OperatorAccount,
} from './api'

const ROLES: { value: string; label: string }[] = [
  { value: 'viewer', label: 'Viewer' },
  { value: 'operator', label: 'Operator' },
  { value: 'dba', label: 'DBA' },
  { value: 'auditor', label: 'Auditor' },
  { value: 'admin', label: 'Admin' },
]

function roleName(role: string): string {
  if (role === 'platform_admin') return 'Platform admin'
  if (role === 'security_auditor') return 'Security auditor'
  return ROLES.find((item) => item.value === role)?.label ?? role
}

function message(reason: unknown, fallback: string): string {
  return reason instanceof ApiError ? reason.message : fallback
}

/** Local operator accounts: add, change role, disable, and reset a password. */
export function OperatorsPanel({ currentUser }: { currentUser: string }) {
  const [operators, setOperators] = useState<OperatorAccount[] | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState('operator')
  const [busy, setBusy] = useState('')
  const [resetFor, setResetFor] = useState<string | null>(null)
  const [resetValue, setResetValue] = useState('')

  const load = useCallback(() => {
    listOperators()
      .then((response) => { setOperators(response.items); setError('') })
      .catch((reason: unknown) => { setError(message(reason, 'Could not load operator accounts.')) })
  }, [])

  useEffect(() => { load() }, [load])

  async function run(key: string, action: () => Promise<unknown>, done: string) {
    setBusy(key)
    setError('')
    setNotice('')
    try {
      await action()
      setNotice(done)
      load()
    } catch (reason) {
      setError(message(reason, 'The change could not be saved.'))
    } finally {
      setBusy('')
    }
  }

  function onCreate(event: FormEvent) {
    event.preventDefault()
    const name = username.trim()
    if (!name || !password) {
      setError('Enter a username and a password.')
      return
    }
    void run('create', async () => {
      await createOperator(name, password, role)
      setUsername('')
      setPassword('')
      setRole('operator')
    }, `${name.toLowerCase()} was added as ${roleName(role)}.`)
  }

  function onReset(event: FormEvent, name: string) {
    event.preventDefault()
    if (!resetValue) {
      setError('Enter the new password.')
      return
    }
    void run(`reset-${name}`, async () => {
      await resetOperatorPassword(name, resetValue)
      setResetFor(null)
      setResetValue('')
    }, `The password for ${name} was reset. Its open sessions are signed out.`)
  }

  const self = currentUser.trim().toLowerCase()

  return (
    <section className="panel operators-panel" aria-labelledby="operators-title">
      <div className="panel-heading">
        <div>
          <h2 id="operators-title"><UserPlus size={18} /> Operators</h2>
          <p>Local accounts. Directory operators are managed in the directory and are not listed here.</p>
        </div>
      </div>
      {error && <div className="inline-error" role="alert">{error}</div>}
      {notice && <p className="inline-success" role="status">{notice}</p>}
      <form className="operator-form" onSubmit={onCreate} aria-label="Add operator">
        <label>
          Username
          <input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="off" aria-label="New operator username" maxLength={80} />
        </label>
        <label>
          Password
          <input value={password} onChange={(event) => setPassword(event.target.value)} type="password" autoComplete="new-password" aria-label="New operator password" maxLength={200} />
        </label>
        <label>
          Role
          <select value={role} onChange={(event) => setRole(event.target.value)} aria-label="New operator role">
            {ROLES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
        </label>
        <button className="button button-primary" type="submit" disabled={busy === 'create'}>
          <UserPlus size={14} /> {busy === 'create' ? 'Adding…' : 'Add operator'}
        </button>
      </form>
      <p className="operator-hint">At least 10 characters with a letter and a digit. The password is stored as a salted hash.</p>
      {operators === null && !error && <p className="operator-hint">Loading operator accounts…</p>}
      {operators && operators.length > 0 && (
        <div className="table-scroll">
          <table className="data-table operators-table">
            <thead>
              <tr>
                <th scope="col">Username</th>
                <th scope="col">Role</th>
                <th scope="col">Status</th>
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {operators.map((account) => {
                const isSelf = account.username === self
                const known = ROLES.some((item) => item.value === account.role)
                return (
                  <tr key={account.username}>
                    <td><span className="primary-cell">{account.username}</span>{isSelf && <span className="operator-self">You</span>}</td>
                    <td>
                      {isSelf ? roleName(account.role) : (
                        <select
                          aria-label={`Role for ${account.username}`}
                          value={account.role}
                          disabled={busy === `role-${account.username}`}
                          onChange={(event) => {
                            const next = event.target.value
                            void run(`role-${account.username}`, () => setOperatorRole(account.username, next), `${account.username} is now ${roleName(next)}.`)
                          }}
                        >
                          {!known && <option value={account.role}>{roleName(account.role)}</option>}
                          {ROLES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                        </select>
                      )}
                    </td>
                    <td><span className={account.enabled ? 'policy-result policy-pass' : 'policy-result policy-unknown'}>{account.enabled ? 'Enabled' : 'Disabled'}</span></td>
                    <td>
                      {isSelf ? <span className="operator-hint">Use Change password in the top bar.</span> : (
                        <div className="operator-actions">
                          <button
                            className="button button-secondary"
                            type="button"
                            disabled={busy === `enabled-${account.username}`}
                            onClick={() => {
                              void run(
                                `enabled-${account.username}`,
                                () => setOperatorEnabled(account.username, !account.enabled),
                                account.enabled ? `${account.username} was disabled and signed out.` : `${account.username} was enabled.`,
                              )
                            }}
                          >
                            {account.enabled ? 'Disable' : 'Enable'}
                          </button>
                          {resetFor === account.username ? (
                            <form className="operator-reset" onSubmit={(event) => onReset(event, account.username)}>
                              <input
                                type="password"
                                autoComplete="new-password"
                                aria-label={`New password for ${account.username}`}
                                value={resetValue}
                                onChange={(event) => setResetValue(event.target.value)}
                                maxLength={200}
                              />
                              <button className="button button-primary" type="submit" disabled={busy === `reset-${account.username}`}>Save</button>
                              <button className="button" type="button" onClick={() => { setResetFor(null); setResetValue('') }}>Cancel</button>
                            </form>
                          ) : (
                            <button className="button button-secondary" type="button" onClick={() => { setResetFor(account.username); setResetValue('') }}>
                              <KeyRound size={13} /> Reset password
                            </button>
                          )}
                        </div>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
