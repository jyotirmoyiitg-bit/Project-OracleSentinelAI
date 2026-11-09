import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { PortalNotification, PortalNotificationListResponse } from '../types'
import { NotificationBell } from './NotificationBell'

const notification: PortalNotification = {
  id: 'notice-1', incident_id: 'incident-1', target_id: 'target-1', target_name: 'FINANCE-PRD',
  severity: 'critical', message: 'Archive destination full', status: 'unread', created_at: '2026-11-09T10:00:00Z',
}

function feed(overrides: Partial<PortalNotificationListResponse> = {}): PortalNotificationListResponse {
  return { items: [notification], total: 1, unread_count: 1, page: 1, page_size: 10, ...overrides }
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function mockFetch(handler: (url: string, init?: RequestInit) => Response) {
  const mock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => Promise.resolve(handler(String(input), init)))
  vi.stubGlobal('fetch', mock)
  return mock
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('Portal notifications', () => {
  it('shows unread alerts, marks them read through the API and preserves that state on remount', async () => {
    let read = false
    const fetch = mockFetch((url, init) => {
      if (init?.method === 'POST' && url.endsWith('/notice-1/read')) {
        read = true
        return jsonResponse({ ...notification, status: 'read' })
      }
      return jsonResponse(feed({
        items: [{ ...notification, status: read ? 'read' : 'unread' }], unread_count: read ? 0 : 1,
      }))
    })
    const user = userEvent.setup()
    const props = { onOpenIncident: vi.fn(), refreshSeconds: 0, refreshKey: 0 }
    const view = render(<NotificationBell {...props} />)
    await user.click(await screen.findByRole('button', { name: 'Notifications (1 unread)' }))
    expect(await screen.findByText('Archive destination full')).toBeInTheDocument()
    expect(screen.getByText('FINANCE-PRD')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Mark as read' }))
    await screen.findByRole('button', { name: 'Notifications (0 unread)' })
    expect(fetch).toHaveBeenCalledWith('/api/v1/portal-notifications/notice-1/read', expect.objectContaining({ method: 'POST' }))
    expect(props.onOpenIncident).not.toHaveBeenCalled()
    view.unmount()
    render(<NotificationBell {...props} />)
    expect(await screen.findByRole('button', { name: 'Notifications (0 unread)' })).toBeInTheDocument()
  })

  it('marks an alert read and opens the linked incident', async () => {
    mockFetch((_url, init) => jsonResponse(init?.method === 'POST' ? { ...notification, status: 'read' } : feed()))
    const openIncident = vi.fn()
    const user = userEvent.setup()
    render(<NotificationBell onOpenIncident={openIncident} refreshSeconds={0} refreshKey={0} />)
    await user.click(await screen.findByRole('button', { name: 'Notifications (1 unread)' }))
    await user.click(await screen.findByRole('button', { name: 'View incident' }))
    await waitFor(() => expect(openIncident).toHaveBeenCalledWith('incident-1'))
    expect(screen.queryByRole('region', { name: 'Notifications' })).not.toBeInTheDocument()
  })

  it('shows an explicit load failure and retries without inventing a zero count', async () => {
    let fail = true
    mockFetch(() => fail ? jsonResponse({ detail: 'Inbox unavailable' }, 503) : jsonResponse(feed({ items: [], total: 0, unread_count: 0 })))
    const user = userEvent.setup()
    render(<NotificationBell onOpenIncident={vi.fn()} refreshSeconds={0} refreshKey={0} />)
    await user.click(await screen.findByRole('button', { name: 'Notifications unavailable' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Inbox unavailable')
    expect(screen.queryByText('No notifications yet.')).not.toBeInTheDocument()
    fail = false
    await user.click(screen.getByRole('button', { name: 'Retry notifications' }))
    expect(await screen.findByText('No notifications yet.')).toBeInTheDocument()
  })

  it('keeps the alert unread and does not navigate when marking read fails', async () => {
    mockFetch((_url, init) => init?.method === 'POST'
      ? jsonResponse({ detail: 'Could not save read status' }, 503) : jsonResponse(feed()))
    const openIncident = vi.fn()
    const user = userEvent.setup()
    render(<NotificationBell onOpenIncident={openIncident} refreshSeconds={0} refreshKey={0} />)
    await user.click(await screen.findByRole('button', { name: 'Notifications (1 unread)' }))
    await user.click(await screen.findByRole('button', { name: 'View incident' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not save read status')
    expect(screen.getByText('Unread')).toBeInTheDocument()
    expect(openIncident).not.toHaveBeenCalled()
  })

  it('paginates notifications using the server-wide unread count', async () => {
    const fetch = mockFetch((url) => jsonResponse(feed({
      total: 11, unread_count: 11, page: url.includes('page=2') ? 2 : 1,
      items: [{ ...notification, message: url.includes('page=2') ? 'Older alert' : notification.message }],
    })))
    const user = userEvent.setup()
    render(<NotificationBell onOpenIncident={vi.fn()} refreshSeconds={0} refreshKey={0} />)
    await user.click(await screen.findByRole('button', { name: 'Notifications (11 unread)' }))
    await screen.findByText('Page 1 of 2')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Next notifications' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Next notifications' }))
    expect(await screen.findByText('Older alert')).toBeInTheDocument()
    expect(fetch).toHaveBeenCalledWith('/api/v1/portal-notifications?page=2&page_size=10', expect.anything())
    expect(screen.getByRole('button', { name: 'Notifications (11 unread)' })).toBeInTheDocument()
  })

  it('dismisses the panel with Escape and returns focus to the bell', async () => {
    mockFetch(() => jsonResponse(feed()))
    const user = userEvent.setup()
    render(<NotificationBell onOpenIncident={vi.fn()} refreshSeconds={0} refreshKey={0} />)
    const bell = await screen.findByRole('button', { name: 'Notifications (1 unread)' })
    await user.click(bell)
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('region', { name: 'Notifications' })).not.toBeInTheDocument()
    expect(bell).toHaveFocus()
  })

  it('refreshes on the configured interval and on manual refresh, and stops when disabled', async () => {
    vi.useFakeTimers()
    const fetch = mockFetch(() => jsonResponse(feed()))
    const props = { onOpenIncident: vi.fn(), refreshSeconds: 15, refreshKey: 0 }
    const view = render(<NotificationBell {...props} />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(fetch).toHaveBeenCalledTimes(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(15000) })
    expect(fetch).toHaveBeenCalledTimes(2)
    view.rerender(<NotificationBell {...props} refreshSeconds={0} refreshKey={1} />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(fetch).toHaveBeenCalledTimes(3)
    await act(async () => { await vi.advanceTimersByTimeAsync(30000) })
    expect(fetch).toHaveBeenCalledTimes(3)
  })
})
