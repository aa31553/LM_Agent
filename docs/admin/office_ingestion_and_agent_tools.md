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
2. File is saved to local storage.
3. The background document job parses Office text.
4. Text is chunked with source metadata:
   - `docx_text`
   - `xlsx_text`
   - `pptx_text`
5. Chunks are embedded and indexed.
6. Document status becomes `ready`.

No Microsoft Office, LibreOffice, or external converter is required. The parser reads OpenXML package contents directly.

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

Run the focused validation:

```powershell
.\.venv\Scripts\python -m pytest tests\test_office_ingestion_and_agent_tools.py
```

Run the full regression suite:

```powershell
.\.venv\Scripts\python -m pytest
```

Successful validation for this change:

- `tests/test_office_ingestion_and_agent_tools.py`: 4 passed
- Full suite: 37 passed, 1 skipped

## Operations Notes

- Empty Office files fail with `Office document did not contain extractable text.`
- Corrupted OpenXML files fail with a 400-level invalid request error.
- Excel ingestion indexes visible sheet rows and includes sheet names in chunk text.
- PowerPoint ingestion indexes text from slides.
- Tool calling is intended for bounded internal tools. Add new tools only through `AgentToolService.tool_schemas()` and `AgentToolService.execute_tool()` so permission checks remain centralized.
