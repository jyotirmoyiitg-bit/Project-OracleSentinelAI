import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { CircleAlert, MessageSquare, Send } from 'lucide-react'
import { ApiError, createChatThread, sendChatMessage } from './api'
import { StatePanel } from './components/StatePanel'
import type { ChatMessage, ChatThread, GuidedCommand } from './types'

function sqlInPrompt(value: string): boolean {
  const text = value.trim()
  if (/\bselect\b[\s\S]{0,300}\bfrom\b/i.test(text)) return true
  if (/\b(insert\s+into|delete\s+from|merge\s+into|truncate\s+table|grant\s+[a-z_]+|revoke\s+[a-z_]+)\b/i.test(text)) return true
  if (/\b(?:alter|drop|create)\s+(?:table|database|system|session|user|index|tablespace|view)\b/i.test(text)) return true
  if (/^(?:insert|update|delete|merge|alter|drop|create|truncate|grant|revoke|commit|rollback)\b/i.test(text)) return true
  return /^(?:select|with)\b/i.test(text) && /\b(?:from|where|join)\b/i.test(text)
}

function providerBadge(thread: ChatThread, latest: ChatMessage | null): string {
  const provider = latest?.provider || thread.provider
  const model = latest?.model || thread.model
  const answeredByModel = latest ? latest.llm_used : thread.llm_enabled
  if (!answeredByModel || provider === 'disabled') return 'No language model'
  if (provider === 'hosted') return model ? `Hosted model · ${model}` : 'Hosted model'
  if (provider === 'local') return model ? `Local model · ${model}` : 'Local model'
  return provider
}

type AnswerParts = {
  kind: 'model' | 'fallback'
  answer: string
  limitations: string
  steps: string[]
  citationNote: string
  reading: string[]
  evidenceLines: string[]
  notes: string[]
}

function parseCopilotAnswer(content: string): AnswerParts {
  const marker = '\n\nDatabase reading:\n'
  const readingAt = content.indexOf(marker)
  const head = readingAt >= 0 ? content.slice(0, readingAt) : content
  const reading = readingAt >= 0 ? content.slice(readingAt + marker.length).split('\n').map((line) => line.trim()).filter(Boolean) : []
  const empty: AnswerParts = {
    kind: 'model', answer: '', limitations: '', steps: [], citationNote: '', reading, evidenceLines: [], notes: [],
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
  return { ...empty, answer: answer.trim(), limitations, steps, citationNote }
}

function evidenceDetail(label: string, detail: string): string {
  const value = detail.trim()
  const name = label.trim()
  if (name && value.toLowerCase().startsWith(name.toLowerCase())) return value.slice(name.length).trim()
  return value
}

function statusLabel(status: string): string {
  if (status === 'answered') return 'Answered'
  if (status === 'unavailable') return 'Unavailable'
  if (status === 'disabled') return 'No model'
  if (status === 'denied') return 'Not authorized'
  return status
}

function paragraphs(text: string): string[] {
  const blocks = text.split(/\n{2,}/).map((block) => block.trim()).filter(Boolean)
  return blocks.length > 0 ? blocks : []
}

export function CopilotPage({
  scopeType,
  scopeId,
  onRefreshStatus,
}: {
  scopeType: ChatThread['scope_type']
  scopeId: string | null
  onRefreshStatus: (refreshing: boolean, callback: () => void) => void
}) {
  const [thread, setThread] = useState<ChatThread | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [draft, setDraft] = useState('')
  const [formError, setFormError] = useState('')
  const [sending, setSending] = useState(false)
  const [turns, setTurns] = useState<{ question: string; answer: ChatMessage }[]>([])

  const openThread = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setThread(await createChatThread(scopeType, scopeId))
    } catch (reason) {
      setThread(null)
      setError(reason instanceof ApiError ? reason.message : 'Unexpected error while opening the copilot.')
    } finally {
      setLoading(false)
    }
  }, [scopeId, scopeType])

  useEffect(() => { void openThread() }, [openThread])
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
      const answer = await sendChatMessage(thread.id, question)
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

  return (
    <main className="page-shell">
      <section className="page-heading">
        <div>
          <div className="eyebrow"><span className="live-dot" /> COPILOT</div>
          <h1>Copilot</h1>
          <p>Answers use collected metrics, incidents, and approved documents. The copilot does not run SQL on Oracle.</p>
        </div>
        <div className="heading-meta">
          <span className="provider-badge" role="status">{providerBadge(thread, latest)}</span>
        </div>
      </section>

      <section className="panel copilot-panel">
        <p className="copilot-disclosure">{thread.disclosure}</p>
        {commands.length > 0 && (
          <div className="copilot-commands" aria-label="Guided commands">
            {commands.map((command) => (
              <button
                key={command.label}
                className="button button-secondary"
                type="button"
                disabled={sending}
                onClick={() => { void ask(command.prompt) }}
              >
                {command.label}
              </button>
            ))}
          </div>
        )}

        <div className="copilot-transcript" aria-live="polite">
          {turns.length === 0 && <p className="empty-inline">Ask about files, memory, sessions, backups, jobs, storage, incidents, or runbooks.</p>}
          {turns.map((turn) => {
            const parts = parseCopilotAnswer(turn.answer.content)
            const prose = paragraphs(parts.answer)
            return (
            <article key={turn.answer.id} className="copilot-turn">
              <p className="copilot-question"><span>Question</span>{turn.question}</p>
              <div className="copilot-answer-card">
                <div className="copilot-answer-head">
                  <h2>Answer</h2>
                  {turn.answer.provider_status && (
                    <span className={`copilot-status copilot-status-${turn.answer.provider_status}`}>{statusLabel(turn.answer.provider_status)}</span>
                  )}
                </div>
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
                          {(tool.summary || tool.reason) && <span>{tool.summary || tool.reason}</span>}
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

        <form className="copilot-form" onSubmit={onSubmit}>
          <label className="copilot-query">
            <MessageSquare size={15} aria-hidden="true" />
            <span className="sr-only">Ask the copilot</span>
            <input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              aria-label="Ask the copilot"
              placeholder="Ask in plain language. SQL is not accepted."
              maxLength={2000}
              disabled={sending}
            />
          </label>
          <button className="button button-primary" type="submit" disabled={sending}>
            <Send size={14} /> {sending ? 'Sending…' : 'Send'}
          </button>
        </form>
        {formError && <div className="inline-error" role="alert"><CircleAlert size={16} /> {formError}</div>}
      </section>
    </main>
  )
}
