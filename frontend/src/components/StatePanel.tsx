import type { ReactNode } from 'react'

export function StatePanel({
  title,
  message,
  action,
  icon,
  tone = 'neutral',
}: {
  title: string
  message: string
  action?: ReactNode
  icon?: ReactNode
  tone?: 'neutral' | 'error'
}) {
  return (
    <section className={`state-panel state-${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
      {icon && <div className="state-icon" aria-hidden="true">{icon}</div>}
      <h2>{title}</h2>
      <p>{message}</p>
      {action}
    </section>
  )
}
