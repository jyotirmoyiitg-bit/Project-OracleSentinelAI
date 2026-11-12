import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { CircleAlert, MessageSquare, Send } from 'lucide-react'
import { ApiError, createChatThread, sendChatMessage } from './api'
import { StatePanel } from './components/StatePanel'
import type { ChatCitation, ChatMessage, ChatThread, GuidedCommand } from './types'

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

function citationText(citation: ChatCitation): string {
  return [citation.kind, citation.label, citation.document_path, citation.heading, citation.source]
    .filter(Boolean)
    .join(' · ')
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
          {turns.map((turn) => (
            <article key={turn.answer.id} className="copilot-turn">
              <p className="copilot-question">{turn.question}</p>
              <p className="copilot-answer">{turn.answer.content}</p>
              {turn.answer.tools.length > 0 && (
                <ul className="copilot-tools">
                  {turn.answer.tools.map((tool, index) => (
                    <li key={`${tool.tool}-${index}`}>
                      <strong>{tool.tool}</strong>
                      <span className={`quality-tag quality-${tool.decision === 'allowed' ? 'measured' : 'error'}`}>{tool.decision}</span>
                      <span>{tool.summary || tool.reason}</span>
                    </li>
                  ))}
                </ul>
              )}
              {turn.answer.citations.length > 0 && (
                <ul className="copilot-citations">
                  {turn.answer.citations.map((citation, index) => (
                    <li key={`${citation.source}-${index}`}>
                      <span>{citationText(citation)}</span>
                      {citation.detail && <p>{citation.detail}</p>}
                    </li>
                  ))}
                </ul>
              )}
            </article>
          ))}
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
