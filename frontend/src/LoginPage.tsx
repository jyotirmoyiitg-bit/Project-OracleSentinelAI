import { useState, type FormEvent } from 'react'
import { CircleAlert } from 'lucide-react'
import { ApiError, login, type AuthSession } from './api'

export function LoginPage({ onSuccess, oidcEnabled = false }: { onSuccess: (session: AuthSession) => void; oidcEnabled?: boolean }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [localAccount, setLocalAccount] = useState(false)
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
      <form className="login-card" onSubmit={onSubmit}>
        <p className="eyebrow">ORACLE SENTINEL</p>
        <h1>Sign in</h1>
        <p>{localAccount ? 'Sign in with a local user id and password.' : 'Sign in with an Active Directory account.'}</p>
        <label>
          Username
          <input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" aria-label="Username" />
        </label>
        <label>
          Password
          <input value={password} onChange={(event) => setPassword(event.target.value)} type="password" autoComplete="current-password" aria-label="Password" />
        </label>
        <label className="login-local">
          <input
            type="checkbox"
            checked={localAccount}
            onChange={(event) => setLocalAccount(event.target.checked)}
          />
          Local
        </label>
        {error && <div className="inline-error" role="alert"><CircleAlert size={16} /> {error}</div>}
        <button className="button button-primary" type="submit" disabled={submitting}>
          {submitting ? 'Signing in…' : 'Sign in'}
        </button>
        {oidcEnabled && <a className="button" href="/api/v1/auth/oidc/start">Sign in with organization</a>}
      </form>
    </main>
  )
}
