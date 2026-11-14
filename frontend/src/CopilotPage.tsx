import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { CircleAlert, Database, MessageSquare, Send, Sparkles, SquarePen } from 'lucide-react'
import { ApiError, createChatThread, getFleet, getTarget, sendChatMessage } from './api'
import { ActiveDatabase } from './components/ActiveDatabase'
import { StatePanel } from './components/StatePanel'
import type { ChatMessage, ChatThread, GuidedCommand, ModelChoiceOption, Target, TargetDetails } from './types'

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

type Verdict = { label: string; status: string; health: string }

type AnswerParts = {
  kind: 'model' | 'fallback'
  verdict: Verdict | null
  answer: string
  limitations: string
  steps: string[]
  citationNote: string
  reading: string[]
  evidenceLines: string[]
  notes: string[]
}

// "Identity. Status OK. Health HEALTHY." An older stored answer may still carry a question id in front.
const VERDICT = /^(?:Q\d{2}\s+)?(.+?)\. Status ([A-Z_]+)\. Health ([A-Z_]+)\.$/

function splitVerdict(text: string): { verdict: Verdict | null; rest: string } {
  const lines = text.split('\n')
  const first = (lines[0] ?? '').trim()
  const match = VERDICT.exec(first)
  if (!match) return { verdict: null, rest: text }
  return { verdict: { label: match[1], status: match[2], health: match[3] }, rest: lines.slice(1).join('\n').trim() }
}

function parseCopilotAnswer(content: string): AnswerParts {
  const marker = '\n\nDatabase reading:\n'
  const readingAt = content.indexOf(marker)
  const head = readingAt >= 0 ? content.slice(0, readingAt) : content
  const reading = readingAt >= 0 ? content.slice(readingAt + marker.length).split('\n').map((line) => line.trim()).filter(Boolean) : []
  const empty: AnswerParts = {
    kind: 'model', verdict: null, answer: '', limitations: '', steps: [], citationNote: '', reading, evidenceLines: [], notes: [],
  }
  if (head.startsWith('The language model is not answering')) {
    const evidenceLines: string[] = []
    const notes: string[] = []
    let inEvidence = false
    for (const line of head.split('\n').map((item) => item.trim()).filter(Boolean)) {
      if (line === 'Collected evidence:') {
        inEvidence = true
        continue
      }
      if (inEvidence && line.startsWith('- ')) {
        evidenceLines.push(line.slice(2))
        continue
      }
      inEvidence = false
      notes.push(line)
    }
    return { ...empty, kind: 'fallback', evidenceLines, notes }
  }
  let answer = head
  let citationNote = ''
  const citationMarker = '\nNo source citation was available, so this assessment stays limited.'
  if (answer.includes(citationMarker)) {
    citationNote = citationMarker.trim()
    answer = answer.replace(citationMarker, '')
  }
  let steps: string[] = []
  const stepsMarker = '\nNext steps: '
  const stepsAt = answer.lastIndexOf(stepsMarker)
  if (stepsAt >= 0) {
    steps = answer.slice(stepsAt + stepsMarker.length).split(';').map((step) => step.trim()).filter(Boolean)
    answer = answer.slice(0, stepsAt)
  }
  let limitations = ''
  const limitsMarker = '\nLimitations: '
  const limitsAt = answer.lastIndexOf(limitsMarker)
  if (limitsAt >= 0) {
    limitations = answer.slice(limitsAt + limitsMarker.length).trim()
    answer = answer.slice(0, limitsAt)
  }
  const { verdict, rest } = splitVerdict(answer.trim())
  return { ...empty, verdict, answer: rest.trim(), limitations, steps, citationNote }
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

function paragraphs(text: string): string[] {
  const blocks = text.split(/\n{2,}/).map((block) => block.trim()).filter(Boolean)
  return blocks.length > 0 ? blocks : []
}

function scopeTitle(scopeType: ChatThread['scope_type']): string {
  if (scopeType === 'database') return 'Database scope'
  if (scopeType === 'incident') return 'Incident scope'
  return 'Fleet scope'
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
    setSending(true)
    setFormError('')
    try {
      const choice = modelChoice ?? launchModelChoice(thread)
      const answer = await sendChatMessage(thread.id, question, choice || undefined)
      setTurns((current) => [...current, { question, answer }])
      setDraft('')
    } catch (reason) {
      setFormError(reason instanceof ApiError ? reason.message : 'Unexpected error while asking the copilot.')
    } finally {
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

  return (
    <main className="page-shell copilot-page">
      <section className="page-heading copilot-heading">
        <div>
          <div className="eyebrow"><span className="live-dot" /> COPILOT</div>
          <h1>Copilot</h1>
          <p>Answers use collected metrics, incidents, approved documents, and fixed read-only database readings. The copilot does not run SQL on Oracle.</p>
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
        ) : (
          <div className="copilot-scope-note">
            <span className="copilot-scope-icon" aria-hidden="true"><Database size={16} /></span>
            <span>
              <strong>{scopeTitle(scopeType)}</strong>
              <small>
                {scopeType === 'incident'
                  ? 'Answers use the stored incident and its database.'
                  : scopeType === 'database'
                    ? 'The selected database is being loaded.'
                    : 'Fleet chat does not read a database. Choose one to ask about its live state.'}
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
              <option value="">Fleet · no database</option>
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

        <div className="copilot-transcript" ref={transcriptRef} role="log" aria-live="polite" aria-relevant="additions">
          {turns.length === 0 && (
            <div className="copilot-empty">
              <span className="copilot-empty-icon" aria-hidden="true"><MessageSquare size={18} /></span>
              <p className="empty-inline">Ask about files, memory, sessions, backups, jobs, storage, incidents, or runbooks. Pick a guided question above or type in plain language.</p>
            </div>
          )}
          {turns.map((turn) => {
            const parts = parseCopilotAnswer(turn.answer.content)
            const prose = paragraphs(parts.answer)
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
                {parts.verdict && (
                  <div className="copilot-verdict">
                    <strong>{parts.verdict.label}</strong>
                    <span className={`copilot-verdict-chip copilot-verdict-${verdictTone(parts.verdict.status)}`}>
                      <small>Status</small>{verdictText(parts.verdict.status)}
                    </span>
                    <span className={`copilot-verdict-chip copilot-verdict-${verdictTone(parts.verdict.health)}`}>
                      <small>Health</small>{verdictText(parts.verdict.health)}
                    </span>
                  </div>
                )}
                {parts.kind === 'fallback' ? (
                  <div className="copilot-fallback">
                    {parts.notes.map((note) => <p key={note}>{note}</p>)}
                  </div>
                ) : (
                  <div className="copilot-prose">
                    {prose.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}
                  </div>
                )}
                {parts.limitations && (
                  <section className="copilot-section copilot-section-limits">
                    <h3>Limitations</h3>
                    <p>{parts.limitations}</p>
                  </section>
                )}
                {parts.steps.length > 0 && (
                  <section className="copilot-section copilot-section-steps">
                    <h3>Next steps</h3>
                    <ol>
                      {parts.steps.map((step, index) => <li key={`${index}-${step}`}>{step}</li>)}
                    </ol>
                  </section>
                )}
                {parts.citationNote && <p className="copilot-note">{parts.citationNote}</p>}
                {parts.evidenceLines.length > 0 && (
                  <section className="copilot-section copilot-section-collected">
                    <h3>Collected evidence</h3>
                    <ul>
                      {parts.evidenceLines.map((line) => <li key={line}>{line}</li>)}
                    </ul>
                  </section>
                )}
                {parts.reading.length > 0 && (
                  <section className="copilot-section copilot-section-reading">
                    <h3>Database reading</h3>
                    <ul>
                      {parts.reading.map((line) => <li key={line}>{line}</li>)}
                    </ul>
                  </section>
                )}
                {turn.answer.tools.length > 0 && (
                  <section className="copilot-section copilot-section-tools">
                    <h3>Functions</h3>
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
                  </section>
                )}
                {turn.answer.citations.length > 0 && (
                  <section className="copilot-section copilot-section-evidence">
                    <h3>Evidence</h3>
                    <ul className="copilot-citations">
                      {turn.answer.citations.map((citation, index) => (
                        <li key={`${citation.source}-${index}`}>
                          <span className={`copilot-kind copilot-kind-${citation.kind}`}>{citation.kind}</span>
                          <strong>{citation.label}</strong>
                          {citation.source && citation.source !== citation.label && <code>{citation.source}</code>}
                          {evidenceDetail(citation.label, citation.detail) && <p>{evidenceDetail(citation.label, citation.detail)}</p>}
                          {(citation.document_path || citation.heading) && (
                            <span className="copilot-citation-path">{[citation.document_path, citation.heading].filter(Boolean).join(' · ')}</span>
                          )}
                        </li>
                      ))}
                    </ul>
                  </section>
                )}
              </div>
            </article>
            )
          })}
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
              placeholder={target ? `Ask about ${target.display_name} in plain language. SQL is not accepted.` : 'Ask in plain language. SQL is not accepted.'}
              maxLength={2000}
              disabled={sending}
            />
          </label>
          <button className="button button-primary copilot-send" type="submit" disabled={sending}>
            <Send size={14} /> {sending ? 'Sending…' : 'Send'}
          </button>
        </form>
        {formError && <div className="inline-error" role="alert"><CircleAlert size={16} /> {formError}</div>}
        </div>
      </section>
    </main>
  )
}
