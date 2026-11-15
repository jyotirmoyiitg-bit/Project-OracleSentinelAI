import { useState, type FormEvent } from 'react'
import { Activity, CircleAlert, ShieldCheck } from 'lucide-react'
import { ApiError, login, type AuthSession } from './api'

export function LoginPage({
  onSuccess,
  oidcEnabled = false,
  directoryEnabled = false,
}: {
  onSuccess: (session: AuthSession) => void
  oidcEnabled?: boolean
  directoryEnabled?: boolean
}) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  // The directory is the default only when one is configured. Otherwise the local account is the only way in.
  const [localAccount, setLocalAccount] = useState(!directoryEnabled)
  const [error, setError] = useState(() => new URLSearchParams(window.location.search).has('sign_in_error') ? 'The organization sign-in could not be completed. Use a local account.' : '')
  const [submitting, setSubmitting] = useState(false)

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    if (!username.trim() || !password) {
      setError('Enter a username and password.')
      return
    }
    setSubmitting(true)
    setError('')
    try {
      onSuccess(await login(username.trim(), password, localAccount))
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : 'Sign-in failed.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="login-shell">
      <form className="login-card" onSubmit={(event) => { void onSubmit(event) }}>
        <div className="login-brand">
          <span className="brand-mark"><Activity size={19} strokeWidth={2.5} /></span>
          <span className="brand-name">oracle<span>sentinel</span></span>
        </div>
        <h1>Sign in</h1>
        <p className="login-lead">Database monitoring and copilot for the Oracle fleet.</p>
        {directoryEnabled ? (
          <div className="login-method" role="radiogroup" aria-label="Account type">
            <button type="button" role="radio" aria-checked={!localAccount} className={!localAccount ? 'login-method-on' : ''} onClick={() => setLocalAccount(false)}>
              Directory account
            </button>
            <button type="button" role="radio" aria-checked={localAccount} className={localAccount ? 'login-method-on' : ''} onClick={() => setLocalAccount(true)}>
              Local account
            </button>
          </div>
        ) : (
          <p className="login-note">Sign in with a local user id and password.</p>
        )}
        <label>
          Username
          <input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" aria-label="Username" autoFocus />
        </label>
        <label>
          Password
          <input value={password} onChange={(event) => setPassword(event.target.value)} type="password" autoComplete="current-password" aria-label="Password" />
        </label>
        {error && <div className="inline-error" role="alert"><CircleAlert size={16} /> {error}</div>}
        <button className="button button-primary login-submit" type="submit" disabled={submitting}>
          {submitting ? 'Signing in…' : 'Sign in'}
        </button>
        {oidcEnabled && <a className="button login-oidc" href="/api/v1/auth/oidc/start">Sign in with organization</a>}
        <p className="login-foot"><ShieldCheck size={13} aria-hidden="true" /> Every sign-in is recorded in the audit trail.</p>
      </form>
    </main>
  )
}
