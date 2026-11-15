import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { CircleAlert, Copy, Database, HardDrive, Layers3, MessageSquare, Search, Send, Sparkles, Square, SquarePen } from 'lucide-react'
import { ApiError, createChatThread, getFleet, getTarget, sendChatMessage } from './api'
import { ActiveDatabase } from './components/ActiveDatabase'
import { Markdown } from './components/Markdown'
import { ReadingTable } from './components/ReadingTable'
import { StatePanel } from './components/StatePanel'
import type { ChatMessage, ChatThread, GuidedCommand, ModelChoiceOption, StructuredAnswer, Target, TargetDetails } from './types'

function sqlInPrompt(value: string): boolean {
  const text = value.trim()
  if (/\bselect\b[\s\S]{0,300}\bfrom\b/i.test(text)) return true
  if (/\b(insert\s+into|delete\s+from|merge\s+into|truncate\s+table|grant\s+[a-z_]+|revoke\s+[a-z_]+)\b/i.test(text)) return true
  if (/\b(?:alter|drop|create)\s+(?:table|database|system|session|user|index|tablespace|view)\b/i.test(text)) return true
  if (/^(?:insert|update|delete|merge|alter|drop|create|truncate|grant|revoke|commit|rollback)\b/i.test(text)) return true
  return /^(?:select|with)\b/i.test(text) && /\b(?:from|where|join)\b/i.test(text)
}

function providerBadge(thread: ChatThread, latest: ChatMessage | null, selected: ModelChoiceOption | undefined): string {
  if (!latest && selected?.ready) {
    if (selected.id === 'local') return `Local model · ${selected.detail}`
    return `Hosted model · ${selected.detail}`
  }
  const provider = latest?.provider || thread.provider
  const model = latest?.model || thread.model
  const answeredByModel = latest ? latest.llm_used : thread.llm_enabled
  if (!answeredByModel || provider === 'disabled') return 'No language model'
  if (provider === 'hosted') return model ? `Hosted model · ${model}` : 'Hosted model'
  if (provider === 'local') return model ? `Local model · ${model}` : 'Local model'
  return provider
}

function launchModelChoice(thread: ChatThread): string {
  const local = thread.model_choices?.choices.find((choice) => choice.id === 'local' && choice.ready)
  return thread.model_choices?.allowed && local ? 'local' : ''
}

/** Only for answers stored before Phase 18, which carry no structured form. */
function legacyStructured(content: string): StructuredAnswer {
  const base: StructuredAnswer = {
    kind: 'model', summary: content, confidence: '', limitations: '', next_steps: [],
    verdict: null, reading: [], citation_note: '', evidence_lines: [], notes: [],
  }
  if (!content.startsWith('The language model is not answering')) return base
  const evidence: string[] = []
  const notes: string[] = []
  let inEvidence = false
  for (const line of content.split('\n').map((item) => item.trim()).filter(Boolean)) {
    if (line === 'Collected evidence:') { inEvidence = true; continue }
    if (inEvidence && line.startsWith('- ')) { evidence.push(line.slice(2)); continue }
    inEvidence = false
    notes.push(line)
  }
  return { ...base, kind: 'fallback', summary: '', evidence_lines: evidence, notes }
}

function confidenceTone(value: string): string {
  if (value === 'high') return 'good'
  if (value === 'medium') return 'warn'
  return 'neutral'
}

function evidenceDetail(label: string, detail: string): string {
  const value = detail.trim()
  const name = label.trim()
  if (name && value.toLowerCase().startsWith(name.toLowerCase())) return value.slice(name.length).trim()
  return value
}

function promptKey(prompt: string): string {
  return prompt.toLowerCase().replace(/[?.!]+$/g, '').replace(/\s+/g, ' ').trim()
}

/** Offer each question once. The first occurrence keeps its group. */
export function uniqueCommands(commands: GuidedCommand[]): GuidedCommand[] {
  const seenPrompts = new Set<string>()
  const seenLabels = new Set<string>()
  const unique: GuidedCommand[] = []
  for (const command of commands) {
    const key = promptKey(command.prompt)
    const labelKey = `${command.group ?? ''}::${command.label.toLowerCase().trim()}`
    if (seenPrompts.has(key) || seenLabels.has(labelKey)) continue
    seenPrompts.add(key)
    seenLabels.add(labelKey)
    unique.push(command)
  }
  return unique
}

function QuestionShelf({
  commands,
  openGroup,
  sending,
  onToggle,
  onAsk,
}: {
  commands: GuidedCommand[]
  openGroup: string | null
  sending: boolean
  onToggle: (name: string | null) => void
  onAsk: (prompt: string) => void
}) {
  const unique = uniqueCommands(commands)
  const common = unique.filter((command) => !command.group)
  const groups = groupedCommands(unique)
  const tabs = [
    ...(common.length > 0 ? [{ name: 'Common', commands: common }] : []),
    ...groups,
  ]
  const selected = tabs.find((tab) => tab.name === openGroup) ?? null
  return (
    <div className="copilot-questions">
      <div className="copilot-questions-head">
        <span className="copilot-section-label">Guided questions</span>
        <span className="copilot-questions-count">{unique.length} available</span>
      </div>
      <div className="copilot-question-tabs" role="tablist" aria-label="Question categories">
        {tabs.map((tab) => {
          const open = tab.name === openGroup
          return (
            <button
              key={tab.name}
              type="button"
              role="tab"
              id={`question-tab-${tab.name}`}
              aria-selected={open}
              aria-expanded={open}
              aria-controls="question-shelf"
              onClick={() => { onToggle(open ? null : tab.name) }}
            >
              {tab.name}
              <span aria-hidden="true">{tab.commands.length}</span>
            </button>
          )
        })}
      </div>
      {selected && (
        <div className="copilot-question-strip" id="question-shelf" role="tabpanel" aria-labelledby={`question-tab-${selected.name}`}>
          {selected.commands.map((command) => (
            <button
              key={`${selected.name}-${command.label}`}
              className="copilot-chip"
              type="button"
              title={command.prompt}
              disabled={sending}
              onClick={() => { onAsk(command.prompt) }}
            >
              {command.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function groupedCommands(commands: GuidedCommand[]): { name: string, commands: GuidedCommand[] }[] {
  const groups: { name: string, commands: GuidedCommand[] }[] = []
  for (const command of commands) {
    if (!command.group) continue
    const existing = groups.find((group) => group.name === command.group)
    if (existing) existing.commands.push(command)
    else groups.push({ name: command.group, commands: [command] })
  }
  return groups
}

function statusLabel(status: string): string {
  if (status === 'answered') return 'Answered'
  if (status === 'unavailable') return 'Unavailable'
  if (status === 'disabled') return 'No model'
  if (status === 'denied') return 'Not authorized'
  return status
}

function verdictTone(value: string): string {
  const folded = value.toUpperCase()
  if (folded === 'OK' || folded === 'HEALTHY') return 'good'
  if (folded === 'CRITICAL' || folded === 'SOURCE_ERROR' || folded === 'PERMISSION_REQUIRED') return 'bad'
  if (folded === 'WARNING' || folded === 'STALE' || folded === 'INSUFFICIENT_DATA') return 'warn'
  return 'neutral'
}

function verdictText(value: string): string {
  return value.replace(/_/g, ' ').toLowerCase()
}

function scopeTitle(scopeType: ChatThread['scope_type']): string {
  if (scopeType === 'database') return 'Database scope'
  if (scopeType === 'incident') return 'Incident scope'
  return 'Fleet scope'
}

const SCOPE_TEXT: Record<ChatThread['scope_type'], { eyebrow: string; title: string; lead: string; empty: string; placeholder: string }> = {
  fleet: {
    eyebrow: 'FLEET COPILOT',
    title: 'Fleet copilot',
    lead: 'Ask one question about every registered database: incidents, health, connected programs, large datafiles, and policy violations. The copilot does not run SQL on Oracle.',
    empty: 'Ask which databases have an incident, run a program, hold a large datafile, or break a policy. Pick a question above, use a finder, or type in plain language.',
    placeholder: 'Ask about the whole fleet in plain language. SQL is not accepted.',
  },
  database: {
    eyebrow: 'DATABASE COPILOT',
    title: 'Database copilot',
    lead: 'Ask about this database: files, memory, sessions, SQL, waits, backups, errors, and jobs. Answers use fixed read-only readings, collected metrics, and approved documents. The copilot does not run SQL on Oracle.',
    empty: 'Ask about files, memory, sessions, backups, jobs, storage, incidents, or runbooks. Pick a guided question above or type in plain language.',
    placeholder: 'Ask in plain language. SQL is not accepted.',
  },
  incident: {
    eyebrow: 'INCIDENT COPILOT',
    title: 'Incident copilot',
    lead: 'Ask about this incident and its database. Answers use the stored incident, fixed read-only readings, and approved documents. The copilot does not run SQL on Oracle.',
    empty: 'Ask what happened before the incident, whether it happened before, or what the runbook says. Pick a guided question above or type in plain language.',
    placeholder: 'Ask about this incident in plain language. SQL is not accepted.',
  },
}

const FLEET_EXAMPLES = [
  'Which databases in the fleet have reported an incident?',
  "Which databases have an active session from 'x_run.exe' or java.exe?",
  'Which databases have a tablespace with a datafile larger than 10 GB?',
  'Which databases are running in violation of organizational policy?',
]
const PROGRAM_NAME = /^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$/

/** Health counts for the fleet card, from the fleet list the page already loaded. */
function fleetCounts(databases: Target[]): { label: string; count: number; tone: string }[] {
  const tally = (status: string) => databases.filter((item) => (item.monitoring_status || item.last_health_status) === status).length
  const known = ['healthy', 'warning', 'critical', 'offline']
  const other = databases.filter((item) => !known.includes(item.monitoring_status || item.last_health_status)).length
  return [
    { label: 'Healthy', count: tally('healthy'), tone: 'good' },
    { label: 'Warning', count: tally('warning'), tone: 'warn' },
    { label: 'Critical', count: tally('critical'), tone: 'bad' },
    { label: 'Down', count: tally('offline'), tone: 'bad' },
    { label: 'Unknown', count: other, tone: 'neutral' },
  ]
}

/** Two quick ways to ask a fleet question whose subject the operator types: program names and a datafile size. */
function FleetFinder({ sending, onAsk }: { sending: boolean; onAsk: (prompt: string) => void }) {
  const [programs, setProgramNames] = useState('')
  const [size, setSize] = useState('10')
  const [error, setError] = useState('')

  function findPrograms(event: FormEvent) {
    event.preventDefault()
    const names = programs.split(/[\s,;]+/).map((name) => name.trim().replace(/^['"]|['"]$/g, '')).filter(Boolean)
    const valid = [...new Set(names.filter((name) => PROGRAM_NAME.test(name)))].slice(0, 8)
    if (valid.length === 0 || valid.length !== new Set(names).size) {
      setError('Enter program or executable names, such as x_run.exe, java.exe. Use letters, digits, dots, dashes, and underscores.')
      return
    }
    setError('')
    onAsk(`Which databases have a session from program ${valid.map((name) => `'${name}'`).join(' or ')}?`)
  }

  function findDatafiles(event: FormEvent) {
    event.preventDefault()
    const gigabytes = Number(size)
    if (!Number.isFinite(gigabytes) || gigabytes <= 0 || gigabytes > 100000) {
      setError('Enter a datafile size in GB greater than zero.')
      return
    }
    setError('')
    onAsk(`Which databases have a tablespace with a datafile larger than ${gigabytes} GB?`)
  }

  return (
    <div className="fleet-finder" aria-label="Fleet finders">
      <form onSubmit={findPrograms}>
        <span className="fleet-finder-icon" aria-hidden="true"><Search size={14} /></span>
        <label>
          <span>Program or executable running</span>
          <input
            aria-label="Program names"
            value={programs}
            onChange={(event) => setProgramNames(event.target.value)}
            placeholder="x_run.exe, lcotp.exe, java.exe"
            maxLength={300}
            disabled={sending}
          />
        </label>
        <button className="button button-secondary" type="submit" disabled={sending}>Find programs</button>
      </form>
      <form onSubmit={findDatafiles}>
        <span className="fleet-finder-icon" aria-hidden="true"><HardDrive size={14} /></span>
        <label>
          <span>Datafile larger than (GB)</span>
          <input
            aria-label="Datafile size in GB"
            type="number"
            min="0.1"
            step="any"
            value={size}
            onChange={(event) => setSize(event.target.value)}
            disabled={sending}
          />
        </label>
        <button className="button button-secondary" type="submit" disabled={sending}>Find datafiles</button>
      </form>
      {error && <div className="inline-error" role="alert"><CircleAlert size={16} /> {error}</div>}
    </div>
  )
}

export function CopilotPage({
  scopeType,
  scopeId,
  onChooseScope,
  onRefreshStatus,
}: {
  scopeType: ChatThread['scope_type']
  scopeId: string | null
  onChooseScope: (databaseId: string | null) => void
  onRefreshStatus: (refreshing: boolean, callback: () => void) => void
}) {
  const [thread, setThread] = useState<ChatThread | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [draft, setDraft] = useState('')
  const [formError, setFormError] = useState('')
  const [sending, setSending] = useState(false)
  const [progress, setProgress] = useState<string[]>([])
  const abortRef = useRef<AbortController | null>(null)
  const [turns, setTurns] = useState<{ question: string; answer: ChatMessage }[]>([])
  const [openGroup, setOpenGroup] = useState<string | null>(null)
  const [modelChoice, setModelChoice] = useState<string | null>(null)
  const [databases, setDatabases] = useState<Target[]>([])
  const [target, setTarget] = useState<TargetDetails | null>(null)
  const transcriptRef = useRef<HTMLDivElement>(null)
  const threadRequest = useRef(0)
  const modelTouched = useRef(false)

  const openThread = useCallback(async () => {
    const requestId = ++threadRequest.current
    setLoading(true)
    setError('')
    setTurns([])
    setDraft('')
    setFormError('')
    try {
      const opened = await createChatThread(scopeType, scopeId)
      if (requestId !== threadRequest.current) return
      setThread(opened)
      if (!modelTouched.current) setModelChoice(launchModelChoice(opened) || null)
    } catch (reason) {
      if (requestId !== threadRequest.current) return
      setThread(null)
      setError(reason instanceof ApiError ? reason.message : 'Unexpected error while opening the copilot.')
    } finally {
      if (requestId === threadRequest.current) setLoading(false)
    }
  }, [scopeId, scopeType])

  useEffect(() => { void openThread() }, [openThread])

  useEffect(() => {
    const controller = new AbortController()
    let active = true
    getFleet({ page: 1, pageSize: 100, environment: '', healthStatus: '' }, controller.signal)
      .then((fleet) => { if (active) setDatabases(fleet.items.filter((item) => item.enabled)) })
      .catch(() => { if (active) setDatabases([]) })
    if (scopeType === 'database' && scopeId) {
      getTarget(scopeId, controller.signal)
        .then((detail) => { if (active) setTarget(detail) })
        .catch(() => { if (active) setTarget(null) })
    } else {
      setTarget(null)
    }
    return () => {
      active = false
      controller.abort()
    }
  }, [scopeId, scopeType])

  useEffect(() => {
    const transcript = transcriptRef.current
    if (transcript) transcript.scrollTop = transcript.scrollHeight
  }, [turns, sending])
  useEffect(() => {
    onRefreshStatus(loading || sending, () => {
      if (!thread) void openThread()
    })
  }, [loading, onRefreshStatus, openThread, sending, thread])

  async function ask(question: string) {
    if (!thread) return
    const controller = new AbortController()
    abortRef.current = controller
    setSending(true)
    setProgress([])
    setFormError('')
    try {
      const choice = modelChoice ?? launchModelChoice(thread)
      const answer = await sendChatMessage(thread.id, question, choice || undefined, {
        signal: controller.signal,
        onEvent: (event) => {
          if (event.name === 'tool' && typeof event.data === 'object' && event.data !== null && 'tool' in event.data) {
            const tool = String((event.data as { tool: unknown }).tool)
            setProgress((current) => (current.includes(tool) ? current : [...current, tool]))
          }
        },
      })
      setTurns((current) => [...current, { question, answer }])
      setDraft('')
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === 'AbortError') {
        setFormError('Stopped.')
      } else {
        setFormError(reason instanceof ApiError ? reason.message : 'Unexpected error while asking the copilot.')
      }
    } finally {
      abortRef.current = null
      setProgress([])
      setSending(false)
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    const question = draft.trim()
    if (!question) {
      setFormError('Enter a question.')
      return
    }
    if (sqlInPrompt(question)) {
      setFormError('SQL cannot be entered in the prompt. Ask in plain language.')
      return
    }
    void ask(question)
  }

  if (loading && !thread) {
    return <main className="page-shell detail-loading"><div className="loading-state" role="status"><span className="spinner" /> Opening copilot…</div></main>
  }
  if (error && !thread) {
    return (
      <main className="page-shell">
        <StatePanel
          tone="error"
          title="Could not open the copilot"
          message={error}
          icon={<CircleAlert size={23} />}
          action={<button className="button button-primary" type="button" onClick={() => { void openThread() }}>Try again</button>}
        />
      </main>
    )
  }
  if (!thread) return null

  const latest = turns.at(-1)?.answer ?? null
  const commands: GuidedCommand[] = latest?.guided_commands?.length ? latest.guided_commands : thread.guided_commands
  const effectiveChoice = modelChoice ?? launchModelChoice(thread)
  const selectedChoice = thread.model_choices?.choices.find((choice) => choice.id === effectiveChoice)
  const disclosure = (effectiveChoice && selectedChoice?.disclosure) || thread.disclosure
  const scopedDatabase = scopeType === 'database' ? scopeId ?? '' : ''
  const text = SCOPE_TEXT[scopeType]
  const fleetScope = scopeType === 'fleet'

  return (
    <main className={fleetScope ? 'page-shell copilot-page copilot-page-fleet' : 'page-shell copilot-page'}>
      <section className="page-heading copilot-heading">
        <div>
          <div className="eyebrow"><span className="live-dot" /> {text.eyebrow}</div>
          <h1>{text.title}</h1>
          <p>{text.lead}</p>
        </div>
        <div className="heading-meta">
          <button className="button button-secondary" type="button" onClick={() => { void openThread() }} disabled={sending || loading}>
            <SquarePen size={14} /> New chat
          </button>
          {thread.model_choices?.allowed && (
            <label className="copilot-model-choice">
              Model
              <select
                aria-label="Language model"
                autoComplete="off"
                value={effectiveChoice}
                disabled={sending}
                onFocus={() => { modelTouched.current = true }}
                onChange={(event) => {
                  if (!modelTouched.current) return
                  setModelChoice(event.target.value)
                }}
              >
                <option value="">Configured default</option>
                {thread.model_choices.choices.map((choice) => (
                  <option key={choice.id} value={choice.id} disabled={!choice.ready}>
                    {choice.ready ? `${choice.label} · ${choice.detail}` : `${choice.label} · not configured`}
                  </option>
                ))}
              </select>
            </label>
          )}
          <span className="provider-badge" role="status">{providerBadge(thread, latest, selectedChoice)}</span>
        </div>
      </section>

      <section className="copilot-scope" aria-label="Copilot scope">
        {scopeType === 'database' && target ? (
          <ActiveDatabase target={target} />
        ) : fleetScope ? (
          <div className="copilot-fleet-card" aria-label="Fleet in scope">
            <span className="copilot-scope-icon" aria-hidden="true"><Layers3 size={16} /></span>
            <span className="copilot-fleet-text">
              <span>Fleet in scope</span>
              <strong>{databases.length === 1 ? '1 database' : `${databases.length} databases`}</strong>
            </span>
            <ul className="copilot-fleet-counts">
              {fleetCounts(databases).filter((item) => item.count > 0).map((item) => (
                <li key={item.label} className={`copilot-verdict-chip copilot-verdict-${item.tone}`}><small>{item.label}</small>{item.count}</li>
              ))}
            </ul>
          </div>
        ) : (
          <div className="copilot-scope-note">
            <span className="copilot-scope-icon" aria-hidden="true"><Database size={16} /></span>
            <span>
              <strong>{scopeTitle(scopeType)}</strong>
              <small>
                {scopeType === 'incident'
                  ? 'Answers use the stored incident and its database.'
                  : 'The selected database is being loaded.'}
              </small>
            </span>
          </div>
        )}
        {databases.length > 0 && (
          <label className="filter-control copilot-scope-select">
            <span className="sr-only">Database scope</span>
            <select
              aria-label="Database scope"
              value={scopedDatabase}
              disabled={sending}
              onChange={(event) => { onChooseScope(event.target.value || null) }}
            >
              <option value="">{fleetScope ? '● Fleet · all databases (active)' : 'Fleet · all databases'}</option>
              {databases.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.id === scopedDatabase ? `● ${item.display_name} (active)` : item.display_name}
                </option>
              ))}
            </select>
          </label>
        )}
      </section>

      <section className="panel copilot-panel">
        <p className="copilot-disclosure"><Sparkles size={13} aria-hidden="true" /> {disclosure}</p>
        {commands.length > 0 && (
          <QuestionShelf
            commands={commands}
            openGroup={openGroup}
            sending={sending}
            onToggle={setOpenGroup}
            onAsk={(prompt) => { void ask(prompt) }}
          />
        )}
        {fleetScope && <FleetFinder sending={sending} onAsk={(prompt) => { void ask(prompt) }} />}

        <div className="copilot-transcript" ref={transcriptRef} role="log" aria-live="polite" aria-relevant="additions">
          {turns.length === 0 && (
            <div className="copilot-empty">
              <span className="copilot-empty-icon" aria-hidden="true"><MessageSquare size={18} /></span>
              <div>
                <p className="empty-inline">{text.empty}</p>
                {fleetScope && (
                  <ul className="copilot-examples" aria-label="Example fleet questions">
                    {FLEET_EXAMPLES.map((example) => (
                      <li key={example}>
                        <button type="button" className="text-button" disabled={sending} onClick={() => { void ask(example) }}>{example}</button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          )}
          {turns.map((turn) => {
            const parts: StructuredAnswer = turn.answer.answer ?? legacyStructured(turn.answer.content)
            return (
            <article key={turn.answer.id} className="copilot-turn">
              <p className="copilot-question"><span>You asked</span>{turn.question}</p>
              <div className="copilot-answer-card">
                <div className="copilot-answer-head">
                  <h2>Answer</h2>
                  {turn.answer.provider_status && (
                    <span className={`copilot-status copilot-status-${turn.answer.provider_status}`}>{statusLabel(turn.answer.provider_status)}</span>
                  )}
                </div>
                {(parts.verdict || parts.confidence) && (
                  <div className="copilot-verdict">
                    {parts.verdict && <strong>{parts.verdict.label}</strong>}
                    {parts.verdict && (
                      <span className={`copilot-verdict-chip copilot-verdict-${verdictTone(parts.verdict.status)}`}>
                        <small>Status</small>{verdictText(parts.verdict.status)}
                      </span>
                    )}
                    {parts.verdict && (
                      <span className={`copilot-verdict-chip copilot-verdict-${verdictTone(parts.verdict.health)}`}>
                        <small>Health</small>{verdictText(parts.verdict.health)}
                      </span>
                    )}
                    {parts.confidence && (
                      <span className={`copilot-verdict-chip copilot-verdict-${confidenceTone(parts.confidence)}`}>
                        <small>Confidence</small>{parts.confidence}
                      </span>
                    )}
                  </div>
                )}
                {parts.kind === 'fallback' && parts.notes.length > 0 ? (
                  <div className="copilot-fallback">
                    {parts.notes.map((note, index) => <p key={index}>{note}</p>)}
                  </div>
                ) : parts.summary ? (
                  <Markdown text={parts.summary} />
                ) : null}
                {parts.limitations && (
                  <section className="copilot-section copilot-section-limits">
                    <h3>Limitations</h3>
                    <p>{parts.limitations}</p>
                  </section>
                )}
                {parts.next_steps.length > 0 && (
                  <section className="copilot-section copilot-section-steps">
                    <h3>Next steps</h3>
                    <ol>
                      {parts.next_steps.map((step, index) => <li key={index}>{step}</li>)}
                    </ol>
                  </section>
                )}
                {parts.citation_note && <p className="copilot-note">{parts.citation_note}</p>}
                {parts.evidence_lines.length > 0 && (
                  <section className="copilot-section copilot-section-collected">
                    <h3>Collected evidence</h3>
                    <ul>
                      {parts.evidence_lines.map((line, index) => <li key={index}>{line}</li>)}
                    </ul>
                  </section>
                )}
                {parts.reading.length > 0 && (
                  <section className="copilot-section copilot-section-reading">
                    <h3>{parts.kind === 'fleet' ? 'Fleet findings' : 'Database reading'}</h3>
                    <ReadingTable lines={parts.reading} named={parts.kind === 'fleet'} />
                  </section>
                )}
                {turn.answer.tools.length > 0 && (
                  <details className="copilot-section copilot-section-tools">
                    <summary><h3>Functions</h3></summary>
                    <ul className="copilot-tools">
                      {turn.answer.tools.map((tool, index) => (
                        <li key={`${tool.tool}-${index}`}>
                          <code>{tool.tool}</code>
                          <span className={`quality-tag quality-${tool.decision === 'allowed' ? 'measured' : 'error'}`}>{tool.decision}</span>
                          {tool.duration_ms > 0 && <span className="copilot-duration">{tool.duration_ms} ms</span>}
                          {(tool.summary || tool.reason) && <span>{(tool.summary || tool.reason).replace(/^Q\d{2}\s+/, '')}</span>}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
                {turn.answer.citations.length > 0 && (
                  <details className="copilot-section copilot-section-evidence">
                    <summary><h3>Evidence</h3></summary>
                    <ul className="copilot-citations">
                      {turn.answer.citations.map((citation, index) => (
                        <li key={`${citation.source}-${index}`}>
                          <span className={`copilot-kind copilot-kind-${citation.kind}`}>{citation.kind}</span>
                          <strong>{citation.label}</strong>
                          {citation.source && citation.source !== citation.label && <code>{citation.source}</code>}
                          {evidenceDetail(citation.label, citation.detail) && <p>{evidenceDetail(citation.label, citation.detail)}</p>}
                          {(citation.document_path || citation.heading) && (
                            <span className="copilot-citation-path">{[citation.document_path, citation.heading].filter(Boolean).join(' - ')}</span>
                          )}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
                <div className="copilot-answer-foot">
                  <span>{[turn.answer.provider, turn.answer.model].filter(Boolean).join(' - ') || 'No language model'}</span>
                  <button
                    className="copilot-copy" type="button"
                    onClick={() => { void navigator.clipboard?.writeText(turn.answer.content).catch(() => undefined) }}
                  >
                    <Copy size={13} /> Copy
                  </button>
                </div>
              </div>
            </article>
            )
          })}
          {sending && (
            <div className="copilot-progress" role="status">
              <span className="spinner" /> Working{progress.length > 0 ? `: ${progress.join(', ')}` : '...'}
            </div>
          )}
        </div>

        <div className="copilot-composer">
        <form className="copilot-form" onSubmit={onSubmit}>
          <label className="copilot-query">
            <MessageSquare size={15} aria-hidden="true" />
            <span className="sr-only">Ask the copilot</span>
            <input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              aria-label="Ask the copilot"
              placeholder={target ? `Ask about ${target.display_name} in plain language. SQL is not accepted.` : text.placeholder}
              maxLength={2000}
              disabled={sending}
            />
          </label>
          <button className="button button-primary copilot-send" type="submit" disabled={sending}>
            <Send size={14} /> {sending ? 'Sending…' : 'Send'}
          </button>
          {sending && (
            <button className="button button-secondary copilot-send" type="button" onClick={() => { abortRef.current?.abort() }}>
              <Square size={14} /> Stop
            </button>
          )}
        </form>
        {formError && <div className="inline-error" role="alert"><CircleAlert size={16} /> {formError}</div>}
        </div>
      </section>
    </main>
  )
}
