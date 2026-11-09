import { useCallback, useEffect, useRef, useState } from 'react'
import { Bell, ChevronLeft, ChevronRight, RefreshCw, X } from 'lucide-react'
import { ApiError, getPortalNotifications, readPortalNotification } from '../api'
import { formatDate } from '../format'
import type { PortalNotification, PortalNotificationListResponse } from '../types'
import { SeverityBadge } from './SeverityBadge'

export function NotificationBell({
  onOpenIncident,
  refreshSeconds,
  refreshKey,
}: {
  onOpenIncident: (id: string) => void
  refreshSeconds: number
  refreshKey: number
}) {
  const [open, setOpen] = useState(false)
  const [feed, setFeed] = useState<PortalNotificationListResponse | null>(null)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const container = useRef<HTMLDivElement>(null)
  const bell = useRef<HTMLButtonElement>(null)
  const requestId = useRef(0)

  const refresh = useCallback(async (signal?: AbortSignal) => {
    const id = ++requestId.current
    setLoading(true)
    setError('')
    try {
      const result = await getPortalNotifications(page, signal)
      if (id !== requestId.current) return
      setFeed(result)
    } catch (reason) {
      if (signal?.aborted || id !== requestId.current) return
      setError(reason instanceof ApiError ? reason.message : 'Could not load notifications.')
    } finally {
      if (id === requestId.current && !signal?.aborted) setLoading(false)
    }
  }, [page])

  useEffect(() => {
    const controller = new AbortController()
    void refresh(controller.signal)
    const timer = refreshSeconds > 0
      ? window.setInterval(() => { void refresh(controller.signal) }, refreshSeconds * 1000)
      : undefined
    return () => {
      controller.abort()
      window.clearInterval(timer)
      requestId.current += 1
    }
  }, [refresh, refreshSeconds, refreshKey])

  useEffect(() => {
    if (!open) return
    function dismiss(event: PointerEvent) {
      if (event.target instanceof Node && !container.current?.contains(event.target)) setOpen(false)
    }
    function escape(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setOpen(false)
        bell.current?.focus()
      }
    }
    document.addEventListener('pointerdown', dismiss)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('pointerdown', dismiss)
      document.removeEventListener('keydown', escape)
    }
  }, [open])

  async function markRead(item: PortalNotification, navigate = false) {
    setBusy(true)
    setError('')
    requestId.current += 1
    setLoading(false)
    try {
      if (item.status === 'unread') await readPortalNotification(item.id)
      await refresh()
      if (navigate) {
        setOpen(false)
        onOpenIncident(item.incident_id)
      }
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : 'Could not mark the notification as read.')
    } finally {
      setBusy(false)
    }
  }

  const unread = feed?.unread_count
  const label = error ? 'Notifications unavailable' : unread === undefined ? 'Notifications loading' : `Notifications (${unread} unread)`
  const pageCount = Math.max(1, Math.ceil((feed?.total ?? 0) / 10))

  return (
    <div className="notification-menu" ref={container}>
      <button
        ref={bell}
        className="icon-button notification-bell"
        aria-label={label}
        aria-expanded={open}
        aria-controls="notification-panel"
        onClick={() => {
          setOpen(!open)
          if (!open) void refresh()
        }}
      >
        <Bell size={18} aria-hidden="true" />
        {error ? <span className="notification-count">!</span> : unread !== undefined && unread > 0 && (
          <span className="notification-count" aria-hidden="true">{unread > 99 ? '99+' : unread}</span>
        )}
      </button>
      {open && (
        <section id="notification-panel" className="notification-panel" aria-label="Notifications">
          <div className="notification-heading">
            <h2>Notifications</h2>
            <button className="icon-button" aria-label="Refresh notifications" disabled={loading || busy} onClick={() => { void refresh() }}>
              <RefreshCw size={15} />
            </button>
            <button className="icon-button" aria-label="Close notifications" onClick={() => { setOpen(false); bell.current?.focus() }}>
              <X size={15} />
            </button>
          </div>
          <p className="notification-note">Shared portal inbox. Reading an alert does not acknowledge the incident.</p>
          {error && (
            <div className="notification-error" role="alert">
              <p>{error}</p>
              <button className="button button-secondary" disabled={busy || loading} onClick={() => { void refresh() }}>Retry notifications</button>
            </div>
          )}
          {loading && <p className="notification-note" role="status">Loading notifications...</p>}
          {feed && (
            <>
              <p className="notification-note" aria-live="polite">{feed.unread_count} unread</p>
              {feed.items.length === 0 && !loading && !error && <p className="notification-note">No notifications yet.</p>}
              <ul className="notification-list">
                {feed.items.map((item) => (
                  <li key={item.id} className={item.status === 'unread' ? 'notification-unread' : ''}>
                    <div className="notification-meta"><SeverityBadge severity={item.severity} /><span>{item.status === 'unread' ? 'Unread' : 'Read'}</span></div>
                    <strong>{item.target_name}</strong>
                    <p>{item.message}</p>
                    <time dateTime={item.created_at}>{formatDate(item.created_at)}</time>
                    <div className="notification-actions">
                      <button className="button button-secondary" disabled={busy} onClick={() => { void markRead(item, true) }}>View incident</button>
                      {item.status === 'unread' && (
                        <button className="button button-secondary" disabled={busy} onClick={() => { void markRead(item) }}>Mark as read</button>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
              <div className="notification-pagination">
                <button className="icon-button" aria-label="Previous notifications" disabled={page <= 1 || loading || busy} onClick={() => setPage(page - 1)}><ChevronLeft size={16} /></button>
                <span>Page {page} of {pageCount}</span>
                <button className="icon-button" aria-label="Next notifications" disabled={page >= pageCount || loading || busy} onClick={() => setPage(page + 1)}><ChevronRight size={16} /></button>
              </div>
            </>
          )}
        </section>
      )}
    </div>
  )
}
