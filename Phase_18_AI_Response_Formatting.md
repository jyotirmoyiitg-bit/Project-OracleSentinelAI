# Phase 18 — Structured AI Answers and Readable Formatting

**Phase:** 18
**Status:** Implemented
**Prerequisites:** [Phase 17 — GraphDB for Every Operator](Phase_17_GraphDB_Access.md), [Phase 6 — AI Copilot](Phase_6_AI_Copilot.md)

## 1. Overview

The copilot already produced a structured `Assessment` (`assessment`, `confidence`, `limitations`, `next_steps`) and a structured catalog envelope, then flattened both into one delimited plain-text string. The portal recovered the structure by searching that string for `\nLimitations: `, `\nNext steps: `, `\n\nDatabase reading:\n`, and a first-line verdict regex. A model sentence containing those words, or a next step containing `;`, broke the split. Phase 18 carries the structure end-to-end, renders the summary as Markdown, and stops re-parsing display text.

## 2. Rules

- `content` stays the plain-text rendering of the answer. It is what is stored in `chat_messages.content`, what history returns, and what existing tests read. It does not change shape.
- Every assistant message event also carries `answer`, the structured form. The portal renders `answer` when present and falls back to `content` as the summary when it is absent (older stored answers, test doubles).
- Structure is never recovered from `content` in the portal. No marker search, no verdict regex, no `;` split.
- `confidence` (`low` | `medium` | `high`) reaches the portal and is drawn as a chip. It was validated before and then discarded.
- The summary is Markdown. It is rendered by a small local renderer (headings, paragraphs, bold, inline code, fenced code, lists, pipe tables) that builds React elements only — no raw HTML from the model is ever injected.
- A draft SQL statement stays a draft: it renders in a code block, is never executed, and the existing `omit_unexecuted_sql` / `neutralize` cleaning still runs before rendering.
- The gateway passes the assessment JSON schema to the provider instead of `None`. Ollama receives it as `format`, OpenAI as `response_format: json_schema`, Anthropic keeps the prompt contract. One invalid model reply gets one repair retry (`Return only valid JSON…`) before the no-model fallback.
- The last turns of the thread are sent as conversation history (bounded, sanitized) so a follow-up question has context. Tool choice stays deterministic; history is data, not instructions.
- SSE is consumed incrementally in the portal. `status` and `tool` events update a live progress line while the answer is being composed; a Stop control aborts the request. The final `message` event still carries the complete answer.
- Functions and Evidence are collapsed by default. Each answer card footers its own provider and model.

## 3. Backend

- `sentinel.ai.types.Answer` gains `structured: dict` — `{kind, summary, confidence, limitations, next_steps, verdict, reading, citation_note, evidence_lines, notes}` where `verdict` is `{label, status, health} | null` and `kind` is `model | catalog | fallback`.
- `Orchestrator._compose` returns the `Assessment` fields separately; a single `_render_text()` builds the legacy `content` string from them so stored text is byte-identical in shape to before.
- Catalog answers build `structured` from the stored `CatalogEnvelope` (`verdict` from label/status/health, `reading` from the selected lines, `limitations`/`next_steps` from the question) instead of from the rendered text.
- `_with_reading` no longer appends to the text only: the reading lines also land in `structured["reading"]`. The text keeps the `Database reading:` block for storage and history.
- `POST /chat/threads/{id}/messages` adds `answer` to the `message` event. `GET …/messages` is unchanged.

## 4. Portal

- `ChatMessage.answer?: StructuredAnswer` in `types.ts`; `parseCopilotAnswer`, `splitVerdict`, and the `VERDICT` regex are deleted from `CopilotPage.tsx`.
- `components/Markdown.tsx` renders the summary. `components/ReadingTable.tsx` renders reading lines as a table (a `… percent used …` line becomes name/detail rows; any other line is a single-cell row in monospace).
- The answer card shows: verdict chips (catalog), confidence chip (model), Markdown summary, Limitations, Next steps (each step intact, `;` included), citation note, Collected evidence (fallback), Database reading table, then collapsed Functions and Evidence, and a provider/model footer with a Copy button.
- While sending, tool events from the stream are listed under a `Working…` line; Stop aborts.

## 5. Acceptance

- [x] A model answer whose next step contains `;` and whose summary contains the word `Limitations:` renders with the step intact and no false Limitations section.
- [x] A catalog answer renders verdict chips from `answer.verdict` with no question id, without parsing the first content line.
- [x] Confidence from the model is visible on the answer card.
- [x] Markdown bold, a pipe table, a list, and a fenced code block in the summary render as elements, not raw markers.
- [x] An invalid first model reply followed by a valid one is repaired and answered; two invalid replies fall back.
- [x] The provider receives the assessment schema (Ollama `format`, OpenAI `json_schema`).
- [x] A follow-up turn includes the prior turn in the messages sent to the model.
- [x] Stored `content`, history, guided commands, SQL refusal, and the no-model fallback are unchanged.

## 6. Tests

- Backend: structured fields on the SSE `message` event for a model answer and for a catalog answer; repair retry; schema forwarded by the local and OpenAI providers; history included in the model call.
- Portal: structured rendering (verdict, confidence, Markdown table/code, `;` step), fallback rendering from `content` alone, incremental SSE progress.

## 7. Out of scope

Token-by-token model streaming (providers are still called non-streaming; SSE progress is event-level), typed tablespace rows from `reading_text.py`, a persisted `structured` column, and any new tool or SQL execution.
