import { useState } from 'react'
import { getDatabaseParameters } from './api'
import { DatabaseView } from './components/DatabaseView'

/** ParameterDB: the initialization parameters in effect, with a filter. Nothing can be changed here. */
export function ParameterPage({ databaseId, onAskCopilot, onOpenDatabase, onRefreshStatus }: {
  databaseId: string
  onAskCopilot: (id: string) => void
  onOpenDatabase: (id: string) => void
  onRefreshStatus: (refreshing: boolean, callback: () => void) => void
}) {
  const [filter, setFilter] = useState('')
  const [changedOnly, setChangedOnly] = useState(false)
  return (
    <DatabaseView
      databaseId={databaseId}
      eyebrow="ParameterDB"
      lead="The initialization parameters in effect for this instance. A parameter changed from its default is marked. This page does not change a parameter."
      load={getDatabaseParameters}
      onAskCopilot={onAskCopilot}
      onOpenDatabase={onOpenDatabase}
      onRefreshStatus={onRefreshStatus}
    >
      {(picture) => {
        const wanted = filter.trim().toLowerCase()
        const rows = picture.parameters.filter((item) => (
          (!changedOnly || !item.is_default)
          && (!wanted || `${item.name} ${item.value} ${item.description}`.toLowerCase().includes(wanted))
        ))
        return (
          <section className="panel" aria-labelledby="oracle-parameters">
            <div className="panel-heading section-heading-wrap">
              <div>
                <h2 id="oracle-parameters">Oracle parameters</h2>
                <p>{picture.version || 'The version banner was not returned.'}</p>
              </div>
              <div className="section-controls">
                <span className="section-figure"><small>Changed from default</small>{picture.changed} of {picture.total}</span>
                <label className="section-check">
                  <input type="checkbox" checked={changedOnly} onChange={(event) => setChangedOnly(event.target.checked)} />
                  Changed only
                </label>
                <label className="section-search">
                  <span className="sr-only">Filter parameters</span>
                  <input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Filter by name, value, or description" aria-label="Filter parameters" />
                </label>
              </div>
            </div>
            <p className="wait-caption section-caption">Showing {rows.length} of {picture.total} parameters.</p>
            <div className="table-scroll section-table-scroll section-table-tall">
              <table className="data-table section-table section-parameters">
                <thead>
                  <tr>
                    <th scope="col">Name</th>
                    <th scope="col">Value</th>
                    <th scope="col">Default</th>
                    <th scope="col">System modifiable</th>
                    <th scope="col">Description</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((item) => (
                    <tr key={item.name} className={item.is_default ? undefined : 'section-changed'}>
                      <td><strong>{item.name}</strong></td>
                      <td className={item.restricted ? 'section-muted' : 'section-value'}>{item.value || '—'}</td>
                      <td>{item.is_default ? 'Yes' : <span className="section-state section-state-warn">No</span>}</td>
                      <td>{item.sys_modifiable === 'FALSE' ? 'No' : item.sys_modifiable.charAt(0) + item.sys_modifiable.slice(1).toLowerCase()}</td>
                      <td className="section-description">{item.description}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {rows.length === 0 && <p className="empty-inline">No parameter matches.</p>}
          </section>
        )
      }}
    </DatabaseView>
  )
}
