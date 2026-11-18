import { useState } from 'react'
import { getGraphStorage, getStorageTablespaces, type GraphStorage, type TablespaceUtilization } from './api'
import { DatabaseView, megabytes, PercentBar, SectionTabs, StackedBars, type Bar } from './components/DatabaseView'

const TABS = [
  ['tablespaces', 'Tablespace utilization'],
  ['redo', 'Online redo'],
  ['recovery', 'Recovery area'],
  ['archive', 'Archive destinations'],
] as const
type Tab = (typeof TABS)[number][0]

interface StoragePicture extends TablespaceUtilization {
  /** The GraphDB storage picture, for redo, the recovery area, and archiving. Null when it could not be read. */
  storage: GraphStorage | null
}

async function loadStorage(id: string, signal: AbortSignal): Promise<StoragePicture> {
  const [tablespaces, storage] = await Promise.all([
    getStorageTablespaces(id, signal),
    getGraphStorage(id, signal).catch((reason: unknown) => {
      if (reason instanceof DOMException && reason.name === 'AbortError') throw reason
      return null
    }),
  ])
  return { ...tablespaces, storage }
}

/** StorageDB: tablespace utilization, online redo, the recovery area, and the archive destinations. */
export function StoragePage({ databaseId, onAskCopilot, onOpenDatabase, onRefreshStatus }: {
  databaseId: string
  onAskCopilot: (id: string) => void
  onOpenDatabase: (id: string) => void
  onRefreshStatus: (refreshing: boolean, callback: () => void) => void
}) {
  const [tab, setTab] = useState<Tab>('tablespaces')
  return (
    <DatabaseView
      databaseId={databaseId}
      eyebrow="StorageDB"
      lead="Disk storage of this database: every tablespace with its free space, the online redo logs, the recovery area, and where archived logs go. This page does not change Oracle."
      load={loadStorage}
      onAskCopilot={onAskCopilot}
      onOpenDatabase={onOpenDatabase}
      onRefreshStatus={onRefreshStatus}
    >
      {(picture) => {
        const storage = picture.storage
        const bars: Bar[] = [...picture.tablespaces]
          .sort((left, right) => (right.total_bytes ?? 0) - (left.total_bytes ?? 0))
          .map((row) => ({
            label: row.name,
            segments: [
              { name: 'Used', value: row.used_bytes ?? 0, tone: 'used' },
              { name: 'Free', value: row.free_bytes ?? 0, tone: 'free' },
            ],
          }))
        return (
          <>
            <SectionTabs label="StorageDB sections" tabs={TABS} active={tab} onChange={setTab} />
            {tab === 'tablespaces' && (
              <div className="section-stack">
                <section className="panel" aria-labelledby="tablespace-table">
                  <div className="panel-heading">
                    <div><h2 id="tablespace-table">Tablespaces</h2><p>{picture.caption}</p></div>
                    {picture.total_display && <span className="section-figure"><small>Used of allocated</small>{picture.used_display} of {picture.total_display}</span>}
                  </div>
                  {picture.tablespaces.length === 0 ? <p className="empty-inline">No tablespace was returned.</p> : (
                    <div className="table-scroll">
                      <table className="data-table section-table">
                        <thead>
                          <tr>
                            <th scope="col">Name</th>
                            <th scope="col" className="section-number">Total (MB)</th>
                            <th scope="col" className="section-number">Free (MB)</th>
                            <th scope="col" className="section-number">Used (MB)</th>
                            <th scope="col">Contents</th>
                            <th scope="col">Status</th>
                            <th scope="col" className="section-number">Fragments</th>
                            <th scope="col">Used</th>
                          </tr>
                        </thead>
                        <tbody>
                          {picture.tablespaces.map((row) => (
                            <tr key={row.name}>
                              <td><strong>{row.name}</strong></td>
                              <td className="section-number">{megabytes(row.total_bytes)}</td>
                              <td className="section-number">{megabytes(row.free_bytes)}</td>
                              <td className="section-number">{megabytes(row.used_bytes)}</td>
                              <td>{row.contents || '—'}</td>
                              <td><span className={row.status === 'ONLINE' ? 'section-state section-state-good' : 'section-state section-state-warn'}>{row.status || '—'}</span></td>
                              <td className="section-number">{row.fragments == null ? '—' : row.fragments.toLocaleString('en-US')}</td>
                              <td><PercentBar percent={row.used_percent} /></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </section>
                {bars.length > 0 && (
                  <section className="panel" aria-labelledby="tablespace-chart">
                    <div className="panel-heading"><div><h2 id="tablespace-chart">Tablespace size</h2><p>Used and free, largest first</p></div></div>
                    <StackedBars
                      title="Tablespace size" unit="Megabytes by tablespace" bars={bars}
                      legend={[{ name: 'Used', tone: 'used' }, { name: 'Free', tone: 'free' }]}
                    />
                  </section>
                )}
              </div>
            )}
            {tab !== 'tablespaces' && !storage && <p className="memory-note section-note">The storage picture could not be read.</p>}
            {tab === 'redo' && storage && (
              <section className="panel" aria-labelledby="online-redo">
                <div className="panel-heading"><div><h2 id="online-redo">Online redo</h2><p>Redo log groups and their members</p></div></div>
                {storage.redo_logs.limitation && <p className="memory-note">{storage.redo_logs.limitation}</p>}
                <ul className="section-facts">
                  <li><span>Groups</span><strong>{storage.redo_logs.groups}</strong></li>
                  <li><span>Total size</span><strong>{storage.redo_logs.total_display || 'Not collected.'}</strong></li>
                  <li><span>Current or active</span><strong>{storage.redo_logs.active_percent == null ? 'Not collected.' : `${storage.redo_logs.active_percent}%`}</strong></li>
                </ul>
                {storage.redo_logs.members.length > 0 ? (
                  <div className="table-scroll">
                    <table className="data-table section-table">
                      <thead><tr><th scope="col" className="section-number">Group</th><th scope="col" className="section-number">Thread</th><th scope="col">Member</th><th scope="col" className="section-number">Size</th><th scope="col">Status</th></tr></thead>
                      <tbody>
                        {storage.redo_logs.members.map((member) => (
                          <tr key={`${member.group}:${member.path}`}>
                            <td className="section-number">{member.group}</td>
                            <td className="section-number">{member.thread}</td>
                            <td className="section-path">{member.path}</td>
                            <td className="section-number">{member.display}</td>
                            <td><span className={member.current ? 'section-state section-state-good' : 'section-state'}>{member.status}</span></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : <p className="empty-inline">{storage.detail ? 'No redo member was returned.' : 'Member file names are shown to an administrator.'}</p>}
              </section>
            )}
            {tab === 'recovery' && storage && (
              <section className="panel" aria-labelledby="recovery-area">
                <div className="panel-heading"><div><h2 id="recovery-area">Recovery area</h2><p>Fast recovery area usage by file type</p></div></div>
                {storage.recovery_area.limitation && <p className="memory-note">{storage.recovery_area.limitation}</p>}
                {!storage.recovery_area.present ? <p className="empty-inline">No recovery area is configured.</p> : (
                  <>
                    <ul className="section-facts">
                      <li><span>Used</span><strong><PercentBar percent={storage.recovery_area.used_percent} /></strong></li>
                      <li><span>Space used</span><strong>{storage.recovery_area.used_display || 'Not collected.'}</strong></li>
                      <li><span>Space limit</span><strong>{storage.recovery_area.limit_display || 'Not collected.'}</strong></li>
                      {storage.recovery_area.name && <li><span>Location</span><strong className="section-path">{storage.recovery_area.name}</strong></li>}
                    </ul>
                    {storage.recovery_area.usage.length > 0 && (
                      <div className="table-scroll">
                        <table className="data-table section-table">
                          <thead><tr><th scope="col">File type</th><th scope="col">Space used</th><th scope="col" className="section-number">Files</th></tr></thead>
                          <tbody>
                            {storage.recovery_area.usage.map((item) => (
                              <tr key={item.file_type}>
                                <td>{item.file_type}</td>
                                <td><PercentBar percent={item.percent_space_used} /></td>
                                <td className="section-number">{item.number_of_files ?? '—'}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </>
                )}
              </section>
            )}
            {tab === 'archive' && storage && (
              <section className="panel" aria-labelledby="archive-destinations">
                <div className="panel-heading"><div><h2 id="archive-destinations">Archive destinations</h2><p>Log mode and where archived redo is written</p></div></div>
                {storage.archive.limitation && <p className="memory-note">{storage.archive.limitation}</p>}
                <ul className="section-facts">
                  <li><span>Log mode</span><strong>{storage.archive.log_mode}</strong></li>
                  <li><span>Latest archived log</span><strong>{storage.archive.latest}</strong></li>
                </ul>
                {storage.archive.destinations.length > 0 ? (
                  <div className="table-scroll">
                    <table className="data-table section-table">
                      <thead><tr><th scope="col">Destination</th><th scope="col">Status</th></tr></thead>
                      <tbody>
                        {storage.archive.destinations.map((item) => (
                          <tr key={item.destination}>
                            <td className="section-path">{item.destination}</td>
                            <td><span className={item.status.toUpperCase() === 'VALID' ? 'section-state section-state-good' : 'section-state section-state-warn'}>{item.status}</span></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : <p className="empty-inline">No archive destination was returned.</p>}
              </section>
            )}
            {tab !== 'tablespaces' && storage?.detail_note && <p className="section-detail-note">{storage.detail_note}</p>}
          </>
        )
      }}
    </DatabaseView>
  )
}
