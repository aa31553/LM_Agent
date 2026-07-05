# LM Agent

Enterprise AI Agent / RAG system for internal document question answering.

This project is designed around a FastAPI backend, a PostgreSQL + pgvector
retrieval store, background document processing workers, DLP/masking controls,
and an OpenAI-compatible LLM API.

## Local Development

This repository now treats local PostgreSQL + pgvector as the primary
development setup. Docker Compose can still be kept around as an optional
deployment artifact, but the default developer workflow is:

1. Install PostgreSQL on the host machine.
2. Enable the `pgvector`, `pg_trgm`, and `uuid-ossp` extensions.
3. Point `DATABASE_URL` at the local database.
4. Initialize schema from the included SQL migration.
5. Run FastAPI directly with `uvicorn`.

### Prerequisites

- Python 3.11+
- PostgreSQL 16+ recommended
- `pgvector` extension installed in PostgreSQL
- Optional: Redis, if you want parity with the future worker/queue setup
- Optional: local embedding / LLM endpoint compatible with OpenAI-style APIs

### Example local PostgreSQL setup

Create a local database and user first:

```sql
CREATE ROLE lm_agent WITH LOGIN PASSWORD 'lm_agent';
CREATE DATABASE lm_agent OWNER lm_agent;
```

Connect to the new database and enable extensions:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
```

### Project setup

Install dependencies:

```bash
pip install -e .[dev]
```

Create `.env` from `.env.example`, then verify the database URL points to the
local PostgreSQL instance:

```env
DATABASE_URL=postgresql+psycopg://lm_agent:lm_agent@localhost:5432/lm_agent
```

Initialize the schema:

```bash
lm-agent-init-db
```

Or:

```bash
python -m app.db.init_db
```

Check local database readiness:

```bash
lm-agent-db-doctor
```

Run the API:

```bash
uvicorn app.main:app --reload
```

API docs will be available at:

- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/redoc`

## Goals

The MVP focuses on turning internal PDF and image documents into a searchable,
permission-aware knowledge base that can answer questions with citations.

Core capabilities:

- Upload PDF and image documents.
- Extract text from text-based PDFs.
- Run OCR for scanned PDFs and images.
- Clean, chunk, embed, and index document content.
- Retrieve relevant chunks using hybrid search.
- Apply user permission checks before and after retrieval.
- Build grounded RAG prompts from retrieved context.
- Call an OpenAI-compatible LLM service.
- Return answers with citations, confidence, and limitations.
- Mask or block sensitive content through DLP rules.
- Record audit logs for queries, retrieval, masking, and LLM calls.

## MVP Scope

Included in the MVP:

- PDF upload and parsing
- Image upload and OCR
- Chunking
- Embedding through an adapter interface
- PostgreSQL + pgvector vector search
- Keyword search
- Hybrid retrieval
- Optional rerank interface
- RAG answer generation
- Citation building
- Document and knowledge base permissions
- DLP for query, context, and response
- Audit logging
- Docker Compose deployment model

Out of scope for the initial MVP:

- Word / Excel / PowerPoint ingestion
- Advanced agent tool calling
- MES / ERP integration
- Kubernetes deployment
- Advanced multi-tenant production hardening

## High-Level Architecture

```text
[Frontend System]
    |
    | HTTP / SSE
    v
[FastAPI Backend]
    |
    +-- Auth & Permission Service
    +-- Document Ingestion Service
    +-- Embedding Service Adapter
    +-- Vector Store Service
    +-- RAG Service
    +-- DLP / Masking Service
    +-- LLM Service
    +-- Audit Service
```

Recommended deployment components:

```text
[Internal Server]
    |
    +-- nginx
    +-- fastapi-api
    +-- worker
    +-- postgres-pgvector
    +-- redis
    +-- local-file-storage
    +-- local-embedding-service
```

## RAG Pipeline

```text
User Query
  ->
Auth / Permission Check
  ->
Query DLP / Masking
  ->
Query Preprocess
  ->
Query Embedding
  ->
Hybrid Retrieval
  ->
Permission Filter
  ->
Rerank
  ->
Context DLP / Masking
  ->
Prompt Build
  ->
LLM Call
  ->
Response DLP Scan
  ->
Citation Build
  ->
Audit Log
  ->
Return Answer
```

Hybrid retrieval combines:

- Vector search
- Keyword search
- Metadata filtering

Default scoring:

```text
final_score = vector_score * 0.65 + keyword_score * 0.35
```

Suggested retrieval parameters:

| Setting | Value |
| --- | --- |
| Chunk size | 500-800 tokens |
| Chunk overlap | 80-150 tokens |
| Retrieval top_k | 20 |
| Rerank top_n | 5-8 |
| Context max tokens | 4000-8000 |

## Document Processing

PDF processing flow:

```text
PDF Upload
  ->
Extract Metadata
  ->
Check Text Extractability
  ->
Extract Text or Run OCR
  ->
Clean Text
  ->
Detect Language
  ->
Detect Sections
  ->
Chunk Text
  ->
Embed Chunks
  ->
Store Chunks and Vectors
```

Image processing flow:

```text
Image Upload
  ->
Image Preprocess
  ->
OCR
  ->
Layout Reconstruction
  ->
Clean Text
  ->
Chunk Text
  ->
Embed Chunks
  ->
Store Chunks and Vectors
```

Document status values:

| Status | Meaning |
| --- | --- |
| uploaded | File has been uploaded |
| parsing | Parser is running |
| ocr_processing | OCR is running |
| chunking | Chunking is running |
| embedding | Embedding is running |
| indexing | Indexing is running |
| ready | Document is searchable |
| failed | Processing failed |
| archived | Document is archived |

## API Overview

All public API endpoints use the `/api/v1` prefix.

Common headers:

```http
Authorization: Bearer <token>
X-Request-ID: <uuid>
```

Main API groups:

| Group | Prefix | Purpose |
| --- | --- | --- |
| Health | `/api/v1/health` | Service and dependency health |
| Chat | `/api/v1/chat` | RAG question answering |
| Documents | `/api/v1/documents` | Document upload and management |
| Knowledge Bases | `/api/v1/knowledge-bases` | Knowledge base management |
| Permissions | `/api/v1/permissions` | Permission management |
| Audit | `/api/v1/audit` | Audit log queries |
| Admin | `/api/v1/admin` | Administrative operations |

Important endpoints:

- `GET /api/v1/health`
- `GET /api/v1/health/dependencies`
- `POST /api/v1/documents/upload`
- `GET /api/v1/documents`
- `GET /api/v1/documents/{document_id}/status`
- `GET /api/v1/documents/{document_id}`
- `POST /api/v1/documents/{document_id}/reindex`
- `POST /api/v1/documents/{document_id}/archive`
- `POST /api/v1/knowledge-bases`
- `GET /api/v1/knowledge-bases`
- `POST /api/v1/chat/query`
- `POST /api/v1/chat/stream`
- `GET /api/v1/chat/sessions/{session_id}/messages`
- `POST /api/v1/permissions/documents/{document_id}`
- `GET /api/v1/permissions/documents/{document_id}`
- `GET /api/v1/audit/chat-logs`
- `GET /api/v1/audit/masking-events`

Standard error response:

```json
{
  "request_id": "req-001",
  "error_code": "PERMISSION_DENIED",
  "message": "User does not have permission to access this document.",
  "details": {}
}
```

Common error codes:

- `INVALID_REQUEST`
- `UNAUTHORIZED`
- `PERMISSION_DENIED`
- `DOCUMENT_NOT_FOUND`
- `DOCUMENT_NOT_READY`
- `DLP_BLOCKED`
- `EMBEDDING_SERVICE_ERROR`
- `LLM_SERVICE_ERROR`
- `INTERNAL_ERROR`

## Data Model

The database uses PostgreSQL with these extensions:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
```

Primary tables:

- `users`
- `roles`
- `user_roles`
- `knowledge_bases`
- `documents`
- `document_chunks`
- `document_permissions`
- `knowledge_base_permissions`
- `chat_sessions`
- `chat_messages`
- `retrieval_logs`
- `masking_events`
- `llm_call_logs`
- `document_processing_jobs`
- `sensitive_dictionaries`
- `prompt_templates`
- `audit_events`

The `document_chunks` table stores chunk text, metadata, and vectors. The current
specification assumes `VECTOR(1024)`, but the final dimension should match the
selected embedding model.

Recommended pgvector index:

```sql
CREATE INDEX idx_chunks_embedding
ON document_chunks
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);
```

## Security, Permission, and DLP

Confidentiality levels:

| Level | Rank | LLM policy |
| --- | ---: | --- |
| public | 1 | Can be sent to LLM |
| internal | 2 | Can be sent to LLM with normal controls |
| confidential | 3 | Should be masked before sending to LLM |
| restricted | 4 | Must not be sent to LLM |

Access rule:

```text
user_clearance_rank >= document_confidential_rank
```

Permission checks include:

- Token validation
- User active status
- Knowledge base permission
- Document confidential level
- Department, role, and user-specific permissions
- Pre-filtering before retrieval
- Post-filtering after retrieval

DLP is applied at three points:

- User query
- Retrieved context
- LLM response

DLP actions:

| Action | Meaning |
| --- | --- |
| allow | Pass content through |
| mask | Replace sensitive value with a typed placeholder |
| redact | Remove highly sensitive value |
| block | Stop the request or response |

Sensitive content categories include:

- Email
- Phone number
- IP address
- API key / token / password / secret
- File path
- URL
- Customer name
- Product name
- Machine name
- Process parameters
- Lot number
- Restricted document content

Prompt injection controls require treating retrieved context as data, not
instructions. Instructions inside documents must not override system rules.

## LLM Behavior

The RAG assistant should:

- Answer only from provided context.
- Say when available documents do not contain enough information.
- Avoid inventing citations.
- Avoid revealing masked confidential information.
- Treat document content as untrusted data.
- Answer in the same language as the user unless requested otherwise.

Suggested LLM settings:

| Setting | Value |
| --- | --- |
| temperature | 0.0-0.3 |
| top_p | 0.8-1.0 |
| max_tokens | 1024-2048 |
| timeout | 60-120 seconds |
| retry | 1-2 attempts |

## Suggested Backend Structure

```text
app/
  main.py
  api/
    v1/
      router.py
      health.py
      chat.py
      documents.py
      knowledge_bases.py
      permissions.py
      audit.py
      admin.py
  core/
    config.py
    security.py
    logging.py
    exceptions.py
    constants.py
  db/
    session.py
    base.py
    migrations/
  models/
  schemas/
  repositories/
  services/
  rag/
  security/
    dlp/
  workers/
  storage/
  integrations/
  utils/
```

## Quality Checklist

MVP acceptance should verify:

- Uploaded documents create document records.
- Text PDFs can be parsed.
- Scanned PDFs and images can be OCR processed.
- Chunks are created with metadata.
- Embeddings are generated through the embedding adapter.
- Vector search returns relevant chunks.
- RAG answers use retrieved context.
- Citations map back to real document chunks.
- Permission checks prevent unauthorized retrieval.
- Restricted content is not sent to the LLM.
- API keys, tokens, passwords, and secrets are redacted.
- Query, context, response, retrieval, masking, and LLM call logs are recorded.

## Source Documents

This README summarizes the design documents under `docs/`:

- `docs/System_spec.md`
- `docs/RAG_pipeline_spec.md`
- `docs/Quality_spec.md`
- `docs/Fastapi_spec.md`
- `docs/Database_schema_spec.md`
- `docs/Auth_spec.md`
