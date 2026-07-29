# Session Attachments and Spreadsheet Analysis Workspace

> Branch: `codex/session-analysis-workspace`<br>
> API version: `0.10.0`<br>
> Updated: 2026-07-30<br>
> Frontend implementation guide:
> [Frontend_File_Upload_Guide.md](Frontend_File_Upload_Guide.md)

This feature separates temporary chat documents from deterministic spreadsheet
analysis. It is designed for an internal 31B non-reasoning LLM: the backend owns
workflow control and calculations, while the model only answers grounded document
questions or explains already-computed results.

## Processing modes

| Input | Route | Processing | LLM role |
|---|---|---|---|
| PDF, DOCX, PPTX, image, text | `POST /api/v1/documents/upload` with `scope=session` | Existing document worker converts to Markdown, chunks, embeds, and indexes only for the session | Answer from retrieved chunks |
| XLSX or CSV for exact analysis | `POST /api/v1/analysis/files/upload` | Stored in `analysis_files`; no chunks or embeddings are created | Optional explanation after calculation |

Large spreadsheets must use the analysis route. They are not inserted into the
knowledge base and are not treated as RAG documents.

## Session attachment chat

`POST /api/v1/chat/query` and `POST /api/v1/chat/stream` accept:

```json
{
  "session_id": "SESSION_UUID",
  "query": "Compare the two attached reports.",
  "knowledge_base_ids": [],
  "attachment_ids": ["DOCUMENT_UUID_1", "DOCUMENT_UUID_2"],
  "retrieval_scope": "attachments_only",
  "top_k": 8,
  "use_rerank": true,
  "use_tools": false
}
```

Retrieval scopes:

| Value | Meaning |
|---|---|
| `auto` | Preserve existing behavior; explicit attachments select attachment-only retrieval |
| `attachments_only` | Retrieve only the listed `attachment_ids` |
| `session_attachments` | Retrieve all ready RAG documents in the session |
| `knowledge_bases_only` | Retrieve only the listed knowledge bases |
| `session_and_knowledge_bases` | Combine session documents and authorized knowledge bases |

The backend validates that every attachment belongs to the same session, is ready,
and is readable by the current principal. Session attachment management routes are:

- `GET /api/v1/chat/sessions/{session_id}/attachments`
- `DELETE /api/v1/chat/sessions/{session_id}/attachments/{document_id}`

`knowledge_base_ids` and `attachment_ids` each accept at most 20 UUIDs. A frontend
uploading multiple files into a new Session must upload the first file, save the
returned `session_id`, and use it for subsequent uploads. Parallel uploads without
a `session_id` create separate Sessions.

## Spreadsheet analysis workflow

1. Upload an XLSX or CSV file.
2. Inspect sheets, columns, inferred types, and sample rows.
3. Build an `AnalysisPlan` in the frontend.
4. Validate the plan against the actual schema.
5. Create a queued analysis job.
6. Run `python -m app.workers.analysis_tasks` in a separate process.
7. Poll the job until it is completed or failed.
8. Render the returned table and chart JSON directly in the frontend.
9. Optionally ask the 31B LLM to explain the completed result JSON.

### Routes

| Method | Route | Purpose |
|---|---|---|
| POST | `/api/v1/analysis/files/upload` | Stream an XLSX/CSV into session storage |
| GET | `/api/v1/analysis/files?session_id=...` | List analysis files for a session |
| GET | `/api/v1/analysis/files/{file_id}/inspect` | Return workbook schema and samples |
| DELETE | `/api/v1/analysis/files/{file_id}` | Delete file and jobs |
| POST | `/api/v1/analysis/plans/validate` | Validate and normalize a whitelist plan |
| POST | `/api/v1/analysis/jobs` | Queue an analysis job |
| GET | `/api/v1/analysis/jobs/{job_id}` | Return status and structured result |
| POST | `/api/v1/analysis/jobs/{job_id}/explain` | Ask the LLM to explain a completed result |

### Supported deterministic operations

- Select columns
- Filter: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `contains`, `in`,
  `is_null`, `not_null`
- Group by up to five columns
- Aggregate: `count`, `sum`, `mean`, sample `std`, `min`, `max`, `count_if`
- Sort aggregated output
- Generate bar, line, or scatter chart payloads

The backend never executes model-generated Python or SQL. Unknown columns,
incompatible numeric operations, unsupported output fields, oversized row counts,
and excessive group counts are rejected before or during execution.

Every aggregation alias must be unique and must not collide with a `group_by`
column name. API 0.10.0 does not yet reject every alias collision, so clients must
enforce this constraint before calling the validation or job endpoints.

### Example plan

```json
{
  "file_id": "FILE_UUID",
  "plan": {
    "sheet": "Production",
    "group_by": ["Machine"],
    "aggregations": [
      {"function": "count", "alias": "sample_count"},
      {"function": "mean", "column": "Yield", "alias": "mean_yield"},
      {"function": "std", "column": "Yield", "alias": "std_yield"},
      {
        "function": "count_if",
        "alias": "below_95",
        "condition": {"column": "Yield", "operator": "lt", "value": 95}
      }
    ],
    "sort": [{"column": "mean_yield", "direction": "asc"}],
    "limit": 1000,
    "charts": [
      {
        "type": "bar",
        "x_field": "Machine",
        "y_field": "mean_yield",
        "title": "Average yield by machine"
      }
    ]
  }
}
```

## Worker and safety model

The analysis API only stores files and queues database jobs. The dedicated worker
claims jobs using PostgreSQL row locking and executes spreadsheet processing in a
killable child process with a configured timeout. This avoids blocking Uvicorn and
allows multiple worker processes without claiming the same job.

The LLM explanation endpoint receives only bounded result JSON. Its fixed prompt
states that calculations are final, numbers must not be changed, and causal claims
must not be invented. DLP masking and the shared LLM concurrency limiter are applied.
No autonomous tool planning is required from the 31B model.

## Retention

Analysis files default to seven days (`ANALYSIS_RETENTION_HOURS=168`). The analysis
worker removes expired files hourly, except files with a running job. Deleting a
chat session also removes its temporary documents, analysis files, analysis jobs,
and local artifacts.
