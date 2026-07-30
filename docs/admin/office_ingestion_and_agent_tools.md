# Office Ingestion and Agent Tool Calling Admin Guide

## Scope

This deployment supports ingestion for:

- Word: `.docx`
- Excel: `.xlsx`
- PowerPoint: `.pptx`
- Existing formats: `.pdf`, `.png`, `.jpg`, `.jpeg`, `.tiff`, `.bmp`

Legacy binary Office files (`.doc`, `.xls`, `.ppt`) are not accepted. Convert them to OpenXML before upload.

## Office Ingestion Flow

Office files use the same indexing pipeline as PDF and image uploads:

1. Upload file through `POST /api/v1/documents/upload`.
2. The original file is saved under `LOCAL_STORAGE_ROOT/originals/`.
3. The Windows document worker starts a bounded child process.
4. `pywin32` starts Word, Excel, or PowerPoint under the worker identity, disables macros and
   link updates, opens the original read-only, and saves a temporary OpenXML copy.
5. The job verifies that the copy is a non-empty OpenXML ZIP and invokes MarkItDown plus the
   local parser on the copy.
6. The temporary copy is deleted.
7. UTF-8 Markdown is saved under `LOCAL_STORAGE_ROOT/markdown/`.
8. Markdown is chunked with `source_type=markdown`, embedded, and indexed.
9. Document status becomes `ready`.

Microsoft Office desktop, `pywin32`, and Windows are required. The document worker account must
be signed in or otherwise licensed and authorized for the organization's Microsoft 365,
Purview/AIP, RMS, or sensitivity-label policy. The implementation does not bypass encryption:
Office must grant the worker account permission to open and create the temporary parser copy.
Run Office-capable workers under a dedicated, initialized Windows user profile; do not use an
unlicensed service identity. Desktop Office automation can display policy or sign-in dialogs, so
production deployments must monitor timeouts and validate representative protected files with
the exact worker identity before accepting traffic.

PDFs and images use the same normalization boundary. Scanned PDFs and direct image uploads
keep the existing local OCR fallback, then write the OCR result into the generated Markdown.
The LLM receives Markdown-derived context only, never the uploaded binary.

## Upload Example

```powershell
curl.exe -X POST "http://127.0.0.1:8000/api/v1/documents/upload" `
  -H "Authorization: Bearer admin" `
  -H "X-Request-ID: office-upload-001" `
  -F "file=@E:\docs\policy.docx" `
  -F "knowledge_base_id=<knowledge-base-uuid>" `
  -F "confidential_level=internal" `
  -F "department=IT" `
  -F "document_type=manual_upload" `
  -F "version=2026-Q3"
```

Check processing:

```powershell
curl.exe -H "Authorization: Bearer admin" `
  "http://127.0.0.1:8000/api/v1/documents/<document-id>/status"
```

Expected final status:

```json
{
  "status": "ready",
  "progress": 100
}
```

## Agent Tool Calling

Tool calling is opt-in per chat request. Existing chat behavior is unchanged unless `use_tools` is set to `true`.

Available tools:

- `search_documents`: searches indexed chunks in the request's allowed knowledge bases.
- `get_document_status`: returns ingestion status and indexing counts for a readable document.

The tool layer keeps the same authorization boundary:

- Knowledge base IDs are constrained to the request's `knowledge_base_ids`.
- Document status lookup checks document read permission.
- Tool calls and results are included in the chat response and audit event metadata.

## Chat Example With Tools

```powershell
curl.exe -X POST "http://127.0.0.1:8000/api/v1/chat/query" `
  -H "Authorization: Bearer admin" `
  -H "Content-Type: application/json" `
  -H "X-Request-ID: agent-tools-001" `
  -d '{
    "knowledge_base_ids": ["<knowledge-base-uuid>"],
    "query": "Find the retention policy and summarize the main controls.",
    "top_k": 8,
    "use_rerank": true,
    "use_tools": true
  }'
```

Response includes `tool_calls` when the model requested a tool:

```json
{
  "answer": "...",
  "tool_calls": [
    {
      "tool_name": "search_documents",
      "arguments": {"query": "retention policy"},
      "result": {"results": []}
    }
  ]
}
```

## LLM Endpoint Requirements

Set the usual OpenAI-compatible chat settings in `.env`:

```env
LLM_BASE_URL=http://127.0.0.1:1234/v1
LLM_API_KEY=
LLM_MODEL=google/gemma-4-12b-qat
LLM_REASONING_EFFORT=none
```

For tool calling, the configured chat endpoint must accept OpenAI-compatible `tools` and `tool_choice` fields and return `message.tool_calls`.

If the selected model or local runtime does not support tool calls, leave `use_tools` as `false` for chat requests.

## Validation

First validate the exact identity used to run both Office-capable workers:

```powershell
.\.venv\Scripts\python -m app.scripts.validate_office_com E:\samples\protected.xlsx
```

Expected output starts with:

```text
OFFICE_COM_OK
```

Run the focused validation:

```powershell
.\.venv\Scripts\python -m pytest tests\test_office_file_preparation.py tests\test_office_ingestion_and_agent_tools.py
```

Run the full regression suite:

```powershell
.\.venv\Scripts\python -m pytest
```

## Operations Notes

- Empty Office files fail with `Office document did not contain extractable text.`
- `OFFICE_COM_UNAVAILABLE`: not Windows, `pywin32` missing, or Office unavailable.
- `OFFICE_APPLICATION_UNAVAILABLE`: Word, Excel, or PowerPoint could not start.
- `OFFICE_OPEN_FAILED`: the identity cannot decrypt/open the file, an opening password is
  required, or the file is damaged.
- `OFFICE_NORMALIZATION_FAILED`: Office opened the file but could not save the temporary copy.
- `OFFICE_OUTPUT_STILL_PROTECTED`: the copy remained encrypted and is not sent to OpenXML parsers.
- Excel ingestion indexes visible sheet rows and includes sheet names in chunk text.
- PowerPoint ingestion indexes text from slides.
- `OFFICE_COM_TIMEOUT_SECONDS` defaults to 300 seconds. The child process is terminated on
  timeout so a hidden Office dialog cannot permanently block a worker.
- Tool calling is intended for bounded internal tools. Add new tools only through `AgentToolService.tool_schemas()` and `AgentToolService.execute_tool()` so permission checks remain centralized.
