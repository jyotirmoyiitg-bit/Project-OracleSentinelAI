import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App'
import type { KnowledgeDocumentList, KnowledgeSearchResponse } from './types'

const documents: KnowledgeDocumentList = {
  items: [{
    id: 'doc-1',
    file_name: 'ora-00257.md',
    relative_path: 'Knowledge/internal-runbooks/ora-00257.md',
    category: 'Knowledge',
    version: 3,
    ingestion_status: 'indexed',
    size_bytes: 420,
    page_count: null,
    quality_score: 0.8,
    embedding_provider: 'local',
    embedding_model: 'local-hashing-v1',
    embedding_disclosure: 'Local embeddings.',
    indexed_at: '2026-11-11T12:00:00Z',
    updated_at: '2026-11-11T12:00:00Z',
    acl_roles: ['operator'],
  }],
  total: 1,
  page: 1,
  page_size: 100,
}

const searchResult: KnowledgeSearchResponse = {
  query: 'ORA-00257',
  retrieval: 'hybrid',
  llm_used: false,
  prioritization: 'Policy passages are labeled authoritative.',
  items: [{
    chunk_id: 'chunk-1',
    excerpt: 'ORA-00257 means the fast recovery area is full.',
    score: 0.42,
    authoritative_policy: false,
    citation: {
      document_id: 'doc-1',
      document_name: 'ora-00257.md',
      relative_path: 'Knowledge/internal-runbooks/ora-00257.md',
      category: 'Knowledge',
      version: 3,
      page_start: null,
      page_end: null,
      heading: 'ORA-00257',
      chunk_index: 0,
    },
  }],
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('Knowledge page', () => {
  beforeEach(() => {
    window.location.hash = '#/knowledge'
    window.localStorage.clear()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('lists indexed documents and shows a cited search result without a language model', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/portal-notifications')) return Promise.resolve(jsonResponse({ items: [], total: 0, unread_count: 0, page: 1, page_size: 10 }))
      if (url.includes('page_size=100') && url.includes('/databases')) return Promise.resolve(jsonResponse({ items: [], total: 0, page: 1, page_size: 100, environments: [] }))
      if (url.includes('/api/v1/knowledge/search')) {
        expect(init?.method).toBe('POST')
        expect(JSON.parse(String(init?.body))).toMatchObject({ query: 'ORA-00257', rerank: true })
        return Promise.resolve(jsonResponse(searchResult))
      }
      if (url.includes('/api/v1/knowledge/documents')) return Promise.resolve(jsonResponse(documents))
      return Promise.resolve(jsonResponse({ detail: 'not found' }, 404))
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Knowledge' })).toBeInTheDocument()
    expect(screen.getByText(/This page does not call a language model/)).toBeInTheDocument()
    expect(await screen.findByText('ora-00257.md')).toBeInTheDocument()
    expect(screen.getByText('indexed')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()

    await user.type(screen.getByLabelText('Search knowledge'), 'ORA-00257')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    expect(await screen.findByText('ORA-00257 means the fast recovery area is full.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Knowledge\/internal-runbooks\/ora-00257.md/ })).toBeInTheDocument()
    expect(screen.getByText(/version 3/)).toBeInTheDocument()
  })
})
