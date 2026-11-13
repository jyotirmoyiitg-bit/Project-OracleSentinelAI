# Knowledge Intake

Drop approved reference files here. The indexer scans `ora-19c-manual/` and `Policy/`, stores their text and embeddings, and never executes document content. Files in `ora-19c-manual/` are stored with the Knowledge category.

- **ora-19c-manual/** — Oracle reference material: `.pdf`, `.docx`, `.txt`, and `.md`
- **Policy/** — Governance documents. They are labeled as policy in search and are not ranked above a better knowledge match unless the search asks for that category.
- **Quarantine/** — Encrypted, corrupt, scanned, oversized, linked, or scanner-flagged files, with a reason in `quarantine.log`

Other extensions are left in place and skipped. Scanned PDFs are quarantined because OCR is disabled. Embeddings are generated locally unless remote embeddings are explicitly approved with a privacy disclosure.

Reindex from the Knowledge page, or `POST /api/v1/knowledge/reindex` with the administrator role. Search works when no language model is configured.
