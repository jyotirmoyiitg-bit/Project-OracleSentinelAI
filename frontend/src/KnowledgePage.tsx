import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { BookOpen, CircleAlert, Library, RefreshCw, Search } from 'lucide-react'
import { ApiError, listKnowledgeDocuments, reindexKnowledge, searchKnowledge } from './api'
import { formatDate, relativeTime } from './format'
import { StatePanel } from './components/StatePanel'
import type { KnowledgeDocument, KnowledgePassage, KnowledgeSearchResponse } from './types'

function citationLabel(passage: KnowledgePassage): string {
  const { citation } = passage
  const pages = citation.page_start == null
    ? null
    : citation.page_end && citation.page_end !== citation.page_start
      ? `Pages ${citation.page_start}–${citation.page_end}`
      : `Page ${citation.page_start}`
  return [
    citation.category,
    citation.relative_path,
    `version ${citation.version}`,
    pages,
    citation.heading || null,
  ].filter(Boolean).join(' · ')
}

export function KnowledgePage({
  refreshSeconds,
  onRefreshStatus,
}: {
  refreshSeconds: number
  onRefreshStatus: (refreshing: boolean, callback: () => void) => void
}) {
  const [documents, setDocuments] = useState<KnowledgeDocument[] | null>(null)
  const [total, setTotal] = useState(0)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('')
  const [results, setResults] = useState<KnowledgeSearchResponse | null>(null)
  const [searchError, setSearchError] = useState('')
  const [searching, setSearching] = useState(false)
  const [reindexMessage, setReindexMessage] = useState('')
  const [reindexing, setReindexing] = useState(false)
  const [lastUpdated, setLastUpdated] = useState<string | null>(null)

  const refresh = useCallback(async (initial = false) => {
    if (initial) setLoading(true)
    setError('')
    try {
      const response = await listKnowledgeDocuments()
      setDocuments(response.items)
      setTotal(response.total)
      setLastUpdated(new Date().toISOString())
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === 'AbortError') return
      setError(reason instanceof ApiError ? reason.message : 'Unexpected error while loading documents.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void refresh(true) }, [refresh])
  useEffect(() => {
    onRefreshStatus(loading || searching || reindexing, () => { void refresh(false) })
  }, [loading, onRefreshStatus, reindexing, refresh, searching])
  useEffect(() => {
    if (refreshSeconds === 0) return
    const timer = window.setInterval(() => { void refresh(false) }, refreshSeconds * 1000)
    return () => window.clearInterval(timer)
  }, [refresh, refreshSeconds])

  async function onSearch(event: FormEvent) {
    event.preventDefault()
    const trimmed = query.trim()
    if (!trimmed) {
      setSearchError('Enter a search query.')
      setResults(null)
      return
    }
    setSearching(true)
    setSearchError('')
    try {
      setResults(await searchKnowledge(trimmed, category))
    } catch (reason) {
      setResults(null)
      setSearchError(reason instanceof ApiError ? reason.message : 'Unexpected error while searching documents.')
    } finally {
      setSearching(false)
    }
  }

  async function onReindex() {
    setReindexing(true)
    setReindexMessage('')
    try {
      const report = await reindexKnowledge()
      setReindexMessage(
        `Indexed ${report.indexed}, updated ${report.updated}, unchanged ${report.unchanged}, `
        + `quarantined ${report.quarantined}, deleted ${report.deleted}.`,
      )
      await refresh(false)
    } catch (reason) {
      setReindexMessage(reason instanceof ApiError ? reason.message : 'Reindex failed.')
    } finally {
      setReindexing(false)
    }
  }

  const visible = (documents ?? []).filter((document) => !category || document.category === category)

  return (
    <main className="page-shell">
      <section className="page-heading">
        <div>
          <div className="eyebrow"><span className="live-dot" /> KNOWLEDGE</div>
          <h1>Knowledge</h1>
          <p>Search approved documents and policy text. This page does not call a language model.</p>
        </div>
        <div className="heading-meta">
          <span className="updated-note"><RefreshCw size={13} /> {lastUpdated ? `Updated ${relativeTime(lastUpdated)}` : 'Waiting for data'}</span>
          <button className="button button-secondary" type="button" onClick={() => { void onReindex() }} disabled={reindexing}>
            <Library size={14} /> {reindexing ? 'Reindexing…' : 'Reindex folders'}
          </button>
        </div>
      </section>

      {reindexMessage && <p className="inline-success" role="status">{reindexMessage}</p>}

      <section className="panel" aria-labelledby="knowledge-search-title">
        <div className="panel-heading">
          <div>
            <h2 id="knowledge-search-title"><Search size={18} /> Document search</h2>
            <p className="knowledge-note">Results include the document, version, page, and section. No model is asked to answer.</p>
          </div>
        </div>
        <form className="knowledge-search" onSubmit={(event) => { void onSearch(event) }}>
          <label className="search-control knowledge-query">
            <Search size={14} aria-hidden="true" />
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              aria-label="Search knowledge"
              placeholder="Search runbooks and policy"
            />
          </label>
          <label className="filter-control">
            <span className="sr-only">Document category</span>
            <select aria-label="Document category" value={category} onChange={(event) => setCategory(event.target.value)}>
              <option value="">Knowledge and policy</option>
              <option value="Knowledge">Knowledge</option>
              <option value="Policy">Policy</option>
            </select>
          </label>
          <button className="button button-primary" type="submit" disabled={searching}>
            {searching ? 'Searching…' : 'Search'}
          </button>
        </form>
        {searchError ? (
          <StatePanel tone="error" title="Search failed" message={searchError} icon={<CircleAlert size={23} />} />
        ) : results && results.items.length === 0 ? (
          <StatePanel title="No matching passages" message="Try a different phrase, or reindex if the document was just added." icon={<BookOpen size={24} />} />
        ) : results ? (
          <ol className="knowledge-results">
            {results.items.map((item) => (
              <li key={item.chunk_id} className="knowledge-hit">
                <p className="knowledge-excerpt">{item.excerpt}</p>
                <p className="knowledge-citation">
                  <button type="button" className="text-button" onClick={() => setQuery(item.citation.document_name)}>
                    {citationLabel(item)}
                  </button>
                  {item.authoritative_policy && <span className="environment-tag">Policy</span>}
                </p>
              </li>
            ))}
          </ol>
        ) : null}
      </section>

      <section className="panel fleet-panel" aria-labelledby="knowledge-table-title">
        <div className="panel-heading">
          <div>
            <h2 id="knowledge-table-title">Approved documents <span className="count-pill">{category ? visible.length : total}</span></h2>
            <p>Name, category, version, ingestion status, and last index time.</p>
          </div>
        </div>
        {error ? (
          <StatePanel
            tone="error"
            title="Could not load documents"
            message={error}
            icon={<CircleAlert size={23} />}
            action={<button className="button button-primary" type="button" onClick={() => { void refresh(false) }}><RefreshCw size={15} /> Try again</button>}
          />
        ) : loading && !documents ? (
          <div className="loading-state" role="status"><span className="spinner" /> Loading documents…</div>
        ) : visible.length === 0 ? (
          <StatePanel title="No documents indexed" message="Add PDF, DOCX, text, or Markdown files to the ora-19c-manual or Policy folders, then reindex." icon={<BookOpen size={24} />} />
        ) : (
          <div className="table-scroll">
            <table className="data-table knowledge-table">
              <thead>
                <tr>
                  <th scope="col">Document</th>
                  <th scope="col">Category</th>
                  <th scope="col">Version</th>
                  <th scope="col">Status</th>
                  <th scope="col">Last indexed</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((document) => (
                  <tr key={document.id}>
                    <td>
                      <div className="primary-cell">{document.file_name}</div>
                      <div className="knowledge-path">{document.relative_path}</div>
                    </td>
                    <td>{document.category}</td>
                    <td>{document.version}</td>
                    <td><span className={`ingestion-badge status-${document.ingestion_status}`}>{document.ingestion_status}</span></td>
                    <td>{document.indexed_at ? formatDate(document.indexed_at) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  )
}
