import { Activity, BellRing, Cpu, FileText, HardDrive, Hourglass, MessageSquare, Network, SlidersHorizontal } from 'lucide-react'
import type { ReactNode } from 'react'

export type DatabaseTab = 'overview' | 'graph' | 'wait' | 'memory' | 'storage' | 'parameters' | 'alerts' | 'events' | 'copilot'

const TABS: { id: DatabaseTab; label: string; icon: ReactNode }[] = [
  { id: 'overview', label: 'Overview', icon: <Activity size={14} /> },
  { id: 'graph', label: 'GraphDB', icon: <Network size={14} /> },
  { id: 'wait', label: 'WaitDB', icon: <Hourglass size={14} /> },
  { id: 'memory', label: 'MemoryDB', icon: <Cpu size={14} /> },
  { id: 'storage', label: 'StorageDB', icon: <HardDrive size={14} /> },
  { id: 'parameters', label: 'ParameterDB', icon: <SlidersHorizontal size={14} /> },
  { id: 'alerts', label: 'AlertDB', icon: <BellRing size={14} /> },
  { id: 'events', label: 'Events', icon: <FileText size={14} /> },
  { id: 'copilot', label: 'Copilot', icon: <MessageSquare size={14} /> },
]

/** The views of one database. Choosing a view keeps the database; choosing a database keeps the view. */
export function DatabaseTabs({ active, onOpen }: { active: DatabaseTab; onOpen: (tab: DatabaseTab) => void }) {
  return (
    <div className="workspace-tabs" role="tablist" aria-label="Database views">
      {TABS.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          aria-selected={tab.id === active}
          className={tab.id === active ? 'workspace-tab workspace-tab-active' : 'workspace-tab'}
          onClick={() => { if (tab.id !== active) onOpen(tab.id) }}
        >
          {tab.icon}
          {tab.label}
        </button>
      ))}
    </div>
  )
}
