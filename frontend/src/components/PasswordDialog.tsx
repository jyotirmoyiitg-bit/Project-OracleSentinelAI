import { useState, type FormEvent } from 'react'
import { CircleAlert, X } from 'lucide-react'
import { ApiError, changeOwnPassword } from '../api'

/** A local operator changes their own password. Directory passwords are changed in the directory. */
export function PasswordDialog({ onClose }: { onClose: () => void }) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [repeat, setRepeat] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [done, setDone] = useState(false)

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    if (!current || !next) {
      setError('Enter the current and the new password.')
      return
    }
    if (next !== repeat) {
      setError('The new password and its repeat do not match.')
      return
    }
    setSaving(true)
    setError('')
    try {
      await changeOwnPassword(current, next)
      setDone(true)
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : 'The password could not be changed.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="dialog-backdrop" role="presentation" onClick={onClose}>
      <div className="dialog-card" role="dialog" aria-modal="true" aria-labelledby="password-dialog-title" onClick={(event) => event.stopPropagation()}>
        <div className="dialog-head">
          <h2 id="password-dialog-title">Change password</h2>
          <button className="icon-button" type="button" aria-label="Close" onClick={onClose}><X size={16} /></button>
        </div>
        {done ? (
          <>
            <p className="inline-success" role="status">Your password was changed. Other sessions for this account are signed out.</p>
            <button className="button button-primary" type="button" onClick={onClose}>Done</button>
          </>
        ) : (
          <form className="dialog-form" onSubmit={(event) => { void onSubmit(event) }}>
            <label>
              Current password
              <input type="password" autoComplete="current-password" value={current} onChange={(event) => setCurrent(event.target.value)} aria-label="Current password" maxLength={200} />
            </label>
            <label>
              New password
              <input type="password" autoComplete="new-password" value={next} onChange={(event) => setNext(event.target.value)} aria-label="New password" maxLength={200} />
            </label>
            <label>
              Repeat new password
              <input type="password" autoComplete="new-password" value={repeat} onChange={(event) => setRepeat(event.target.value)} aria-label="Repeat new password" maxLength={200} />
            </label>
            <p className="operator-hint">At least 10 characters with a letter and a digit.</p>
            {error && <div className="inline-error" role="alert"><CircleAlert size={16} /> {error}</div>}
            <div className="dialog-actions">
              <button className="button" type="button" onClick={onClose}>Cancel</button>
              <button className="button button-primary" type="submit" disabled={saving}>{saving ? 'Saving…' : 'Change password'}</button>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}
