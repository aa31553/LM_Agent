# LLMWiki Admin and Usage Guide

## Purpose

LLMWiki is the durable knowledge layer beside the RAG pipeline. RAG retrieves raw
chunks for each question; LLMWiki compiles selected accessible chunks into
persistent topic pages so knowledge can accumulate over time.

This follows the Karpathy-style pattern of immutable sources plus compiled wiki
pages, adapted for LM Agent:

- Raw truth remains in uploaded documents and `document_chunks`.
- Compiled knowledge is stored locally in PostgreSQL.
- Every compiled page keeps evidence links back to chunk and document IDs.
- Linting reports stale pages, missing evidence, weak cross-links, and candidate
  topics to compile next.
- No external wiki program, agent skill, or background service is required.

## Data Model

LLMWiki uses three local tables:

| Table | Purpose |
| --- | --- |
| `llmwiki_pages` | Persistent topic pages with summaries, key points, related topics, fingerprints, and markdown content |
| `llmwiki_page_evidence` | Ranked source chunks supporting each compiled page |
| `llmwiki_operation_logs` | Compile and lint operation history |

The service also runs `CREATE TABLE IF NOT EXISTS` on first use so an existing
local development database can use LLMWiki before a full schema re-init.

## Workflow

1. Upload and process documents until they are `ready`.
2. Open the frontend `LLMWiki` tab.
3. Choose a knowledge base and enter a topic.
4. Use `Preview` to inspect an ephemeral page.
5. Use `Compile` to persist the page.
6. Use `Index` to browse compiled pages.
7. Use `Lint` to find stale pages and candidate concepts.

## Use in Chat QA

Compiled pages are automatically added to chat prompt context alongside RAG
chunks when the user's query matches pages in the selected knowledge bases.

The prompt separates the sources:

- `Context`: raw RAG chunks selected at query time.
- `LLMWiki compiled knowledge context`: persistent compiled topic pages.
- `Image context`: extracted figure/table/image context, when relevant.

If raw retrieval returns no chunks but compiled wiki context exists, chat still
calls the LLM with the wiki context. This lets maintained topic pages answer
questions even when the current query is better served by accumulated synthesis
than by a single raw chunk.

Audit events for successful chat queries include:

```json
{
  "llmwiki_context_used": true
}
```

## API Examples

Compile a page:

```powershell
curl.exe -X POST "http://127.0.0.1:8000/api/v1/llmwiki/topics/AlphaTopic/compile?knowledge_base_ids=<kb-id>&top_k=24" `
  -H "Authorization: Bearer admin"
```

List compiled pages:

```powershell
curl.exe "http://127.0.0.1:8000/api/v1/llmwiki/index?knowledge_base_ids=<kb-id>" `
  -H "Authorization: Bearer admin"
```

Lint a wiki:

```powershell
curl.exe "http://127.0.0.1:8000/api/v1/llmwiki/lint?knowledge_base_ids=<kb-id>" `
  -H "Authorization: Bearer admin"
```

Show the built-in frontend demo content:

```powershell
curl.exe "http://127.0.0.1:8000/api/v1/llmwiki/demo" `
  -H "Authorization: Bearer admin"
```

## Compilation Behavior

The compiler is deterministic and local:

- Ranks candidate chunks by pg_trgm similarity, topic/title/section hits, term
  overlap, source type, and document diversity.
- Extracts English and Chinese terms without external segmentation tools.
- Extracts sentence-level key points using punctuation-aware English/Chinese
  sentence splitting.
- Builds markdown with Summary, Key Points, Evidence, See Also, Contradictions,
  and Maintenance Notes.
- Stores a fingerprint of the source evidence so `Index` and `Lint` can flag
  stale pages after new documents or chunks change the available evidence.

## Permission Boundary

LLMWiki uses the same authorization boundary as RAG retrieval:

- Knowledge base permissions are enforced.
- Document permissions and confidentiality clearance are enforced through the
  existing retrieval permission filter.
- Denied users receive empty/no compiled knowledge rather than hidden evidence.

## Validation

Run the focused tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_llmwiki_api.py -q
```

Expected result after this feature:

```text
2 passed
```
