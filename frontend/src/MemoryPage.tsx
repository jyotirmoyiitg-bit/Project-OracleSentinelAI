import { useState } from 'react'
import { getGraphMemory, getMemorySga, type GraphMemory, type SgaBreakdown } from './api'
import { DatabaseView, megabytes, PercentBar, SectionTabs, StackedBars, type Bar } from './components/DatabaseView'

const TABS = [
  ['sga', 'SGA utilization'],
  ['shared', 'Shared pool'],
  ['buffer', 'Buffer cache'],
  ['pga', 'PGA'],
] as const
type Tab = (typeof TABS)[number][0]

interface MemoryPicture extends SgaBreakdown {
  /** The GraphDB memory picture, for the hit ratio and the PGA. Null when it could not be read. */
  memory: GraphMemory | null
}

async function loadMemory(id: string, signal: AbortSignal): Promise<MemoryPicture> {
  const [sga, memory] = await Promise.all([
    getMemorySga(id, signal),
    getGraphMemory(id, signal).catch((reason: unknown) => {
      if (reason instanceof DOMException && reason.name === 'AbortError') throw reason
      return null
    }),
  ])
  return { ...sga, memory }
}

/** MemoryDB: how the SGA is divided, what fills the shared pool, the buffer cache, and the PGA. */
export function MemoryPage({ databaseId, onAskCopilot, onOpenDatabase, onRefreshStatus }: {
  databaseId: string
  onAskCopilot: (id: string) => void
  onOpenDatabase: (id: string) => void
  onRefreshStatus: (refreshing: boolean, callback: () => void) => void
}) {
  const [tab, setTab] = useState<Tab>('sga')
  const [filter, setFilter] = useState('')
  return (
    <DatabaseView
      databaseId={databaseId}
      eyebrow="MemoryDB"
      lead="How this instance uses its memory: the SGA by pool, the shared pool, the buffer cache, and the PGA. This page does not change Oracle."
      load={loadMemory}
      onAskCopilot={onAskCopilot}
      onOpenDatabase={onOpenDatabase}
      onRefreshStatus={onRefreshStatus}
    >
      {(picture) => {
        const shared = picture.pools.find((pool) => pool.name === 'shared pool')
        const buffer = picture.pools.find((pool) => pool.name === 'buffer cache')
        const wanted = filter.trim().toLowerCase()
        const components = picture.components.filter((item) => !wanted || `${item.pool} ${item.name}`.toLowerCase().includes(wanted))
        const poolBars: Bar[] = picture.pools.map((pool) => ({
          label: pool.name,
          segments: [
            { name: 'Used', value: pool.used_bytes, tone: 'used' },
            { name: 'Free memory', value: pool.free_bytes, tone: 'free' },
          ],
        }))
        const sharedBars: Bar[] = picture.shared_pool.slice(0, 15).map((item) => ({
          label: item.name,
          segments: [{ name: item.name === 'free memory' ? 'Free memory' : 'Used', value: item.bytes, tone: item.name === 'free memory' ? 'free' : 'used' }],
        }))
        return (
          <>
            <SectionTabs label="MemoryDB sections" tabs={TABS} active={tab} onChange={setTab} />
            {tab === 'sga' && (
              <div className="section-stack">
                <section className="panel" aria-labelledby="sga-utilization">
                  <div className="panel-heading">
                    <div><h2 id="sga-utilization">SGA utilization</h2><p>{picture.caption}</p></div>
                    {picture.total_display && <span className="section-figure"><small>Total SGA</small>{picture.total_display}</span>}
                  </div>
                  {picture.pools.length === 0 ? <p className="empty-inline">No SGA pool was returned.</p> : (
                    <StackedBars
                      title="SGA utilization" unit="Megabytes by pool" bars={poolBars}
                      legend={[{ name: 'Used', tone: 'used' }, { name: 'Free memory', tone: 'free' }]}
                    />
                  )}
                </section>
                <section className="panel" aria-labelledby="sga-components">
                  <div className="panel-heading">
                    <div><h2 id="sga-components">SGA components</h2><p>Largest first</p></div>
                    <label className="section-search">
                      <span className="sr-only">Filter components</span>
                      <input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Filter by pool or name" aria-label="Filter components" />
                    </label>
                  </div>
                  <div className="table-scroll section-table-scroll">
                    <table className="data-table section-table">
                      <thead><tr><th scope="col">Pool</th><th scope="col">Name</th><th scope="col" className="section-number">Size (MB)</th><th scope="col" className="section-number">Size (KB)</th></tr></thead>
                      <tbody>
                        {components.map((item) => (
                          <tr key={`${item.pool}:${item.name}`}>
                            <td>{item.pool}</td>
                            <td>{item.name}</td>
                            <td className="section-number">{megabytes(item.bytes)}</td>
                            <td className="section-number">{Math.round(item.bytes / 1024).toLocaleString('en-US')}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {components.length === 0 && <p className="empty-inline">No component matches.</p>}
                </section>
              </div>
            )}
            {tab === 'shared' && (
              <section className="panel" aria-labelledby="shared-pool">
                <div className="panel-heading">
                  <div><h2 id="shared-pool">Shared pool utilization</h2><p>The 15 largest areas of the shared pool, with its free memory</p></div>
                  {shared && <span className="section-figure"><small>Used</small><PercentBar percent={shared.used_percent} /></span>}
                </div>
                {sharedBars.length === 0 ? <p className="empty-inline">No shared pool area was returned.</p> : (
                  <StackedBars
                    title="Shared pool utilization" unit="Megabytes by area" bars={sharedBars}
                    legend={[{ name: 'Used', tone: 'used' }, { name: 'Free memory', tone: 'free' }]}
                  />
                )}
                {shared && <p className="wait-caption section-caption">Shared pool {shared.display}: {megabytes(shared.used_bytes)} MB used and {megabytes(shared.free_bytes)} MB free.</p>}
              </section>
            )}
            {tab === 'buffer' && (
              <section className="panel" aria-labelledby="buffer-cache">
                <div className="panel-heading"><div><h2 id="buffer-cache">Buffer cache</h2><p>Size now, and the hit ratio since instance startup</p></div></div>
                <ul className="section-facts">
                  <li><span>Buffer cache size</span><strong>{buffer ? buffer.display : 'Not collected.'}</strong></li>
                  <li><span>Share of the SGA</span><strong>{buffer && picture.total_bytes > 0 ? `${(buffer.bytes / picture.total_bytes * 100).toFixed(1)}%` : 'Not collected.'}</strong></li>
                  <li>
                    <span>Buffer cache hit ratio</span>
                    <strong>{picture.memory?.hit_ratio.percent != null ? `${picture.memory.hit_ratio.percent}%` : (picture.memory?.hit_ratio.limitation ?? 'Not collected.')}</strong>
                    {picture.memory?.hit_ratio.caption && <small>{picture.memory.hit_ratio.caption}</small>}
                  </li>
                </ul>
                <div className="table-scroll">
                  <table className="data-table section-table">
                    <thead><tr><th scope="col">Component</th><th scope="col" className="section-number">Size (MB)</th></tr></thead>
                    <tbody>
                      {picture.components.filter((item) => item.pool === 'buffer cache').map((item) => (
                        <tr key={item.name}><td>{item.name}</td><td className="section-number">{megabytes(item.bytes)}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}
            {tab === 'pga' && (
              <section className="panel" aria-labelledby="pga-memory">
                <div className="panel-heading"><div><h2 id="pga-memory">PGA</h2><p>Program global area: what server processes have allocated</p></div></div>
                {!picture.memory && <p className="memory-note">The PGA reading could not be read.</p>}
                {picture.memory?.pga.limitation && <p className="memory-note">{picture.memory.pga.limitation}</p>}
                {picture.memory && picture.memory.pga.items.length > 0 && (
                  <ul className="section-facts">
                    {picture.memory.pga.items.map((item) => <li key={item.name}><span>{item.name}</span><strong>{item.display}</strong></li>)}
                  </ul>
                )}
                {picture.memory?.pga.high && <p className="memory-note">PGA allocated is above its target.</p>}
                {picture.memory?.memory_management.lines.map((line) => <p className="wait-caption section-caption" key={line}>{line}</p>)}
              </section>
            )}
          </>
        )
      }}
    </DatabaseView>
  )
}
