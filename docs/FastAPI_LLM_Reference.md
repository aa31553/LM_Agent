---
document_id: lm-agent-fastapi-llm-reference
document_type: api_contract_companion
language: zh-TW
api_name: LM Agent API
api_version: 0.7.0
base_path: /api/v1
branch: codex/llmwiki-feature
updated_at: 2026-07-27
canonical_runtime_schema: GET /openapi.json
human_guide: docs/FastAPI_Human_Guide.md
legacy_detail: docs/Fastapi_spec.md
---

# LM Agent FastAPI Reference（LLM / Agent 版）

## 0. 使用規則

此文件是供 LLM、Agent、程式碼產生器與檢索系統使用的穩定索引，不取代執行時 OpenAPI。

來源優先序（高到低）：

1. 執行中服務的 `GET /openapi.json`。
2. `app/api/v1/*.py` 路由與 `app/schemas/*.py` Pydantic model。
3. 本文件。
4. `docs/Fastapi_spec.md` 的歷史範例。

生成 client 或 request 時不得推測未列欄位。UUID 使用 RFC 4122 字串。時間使用 API schema 定義的 datetime 字串。除明確標為 public 的路由外，預設需要 bearer token。

## 1. 全域契約

```yaml
service:
  title: LM Agent API
  version: 0.7.0
  base_path: /api/v1
  docs:
    swagger: /docs
    redoc: /redoc
    openapi: /openapi.json
  content_types:
    default_request: application/json
    default_response: application/json
    upload: multipart/form-data
    stream: text/event-stream

headers:
  Authorization:
    format: "Bearer <token>"
    required: true
    exceptions:
      - GET /api/v1/health
      - GET /api/v1/health/dependencies
      - GET /api/v1/llmwiki/demo
  X-Request-ID:
    type: string
    required: false
    echoed_by_selected_responses_and_errors: true

local_development_token:
  syntax: "<external_user_id>|<department>|<clearance>|<comma_separated_roles>"
  admin_literal: "admin"
  warning: "development parser; not a production JWT/SSO contract"
```

## 2. 列舉

```yaml
ConfidentialLevel:
  - public
  - internal
  - confidential
  - restricted
DocumentStatus:
  - uploaded
  - parsing
  - ocr_processing
  - chunking
  - embedding
  - indexing
  - ready
  - failed
  - archived
DocumentScope:
  - knowledge_base
  - session
PermissionSubjectType:
  - user
  - role
  - department
PermissionLevel:
  - read
  - write
  - admin
RiskLevel:
  - low
  - medium
  - high
  - insufficient
ErrorCode:
  - INVALID_REQUEST
  - UNAUTHORIZED
  - PERMISSION_DENIED
  - DOCUMENT_NOT_FOUND
  - DOCUMENT_NOT_READY
  - SKILL_NOT_FOUND
  - DLP_BLOCKED
  - EMBEDDING_SERVICE_ERROR
  - LLM_SERVICE_ERROR
  - INTERNAL_ERROR
```

機密等級 rank：`public=1`、`internal=2`、`confidential=3`、`restricted=4`。

## 3. 權限決策

```yaml
document_read:
  - if principal.is_active is false: deny
  - if role contains admin: allow
  - if document.status is archived: deny
  - if document.session_id is not null:
      allow_only: session owner or admin
  - require: principal.clearance_rank >= document.confidentiality_rank
  - if document.department equals principal.department: allow
  - require: knowledge_base allow-list match, unless KB has zero rules
  - require: document allow-list match, unless document has zero rules

knowledge_base_read:
  - if principal.is_active is false: deny
  - if role contains admin: allow
  - if knowledge_base.owner_department equals principal.department: allow
  - otherwise:
      require: KB allow-list match, unless KB has zero rules

allow_list_match:
  accepted_permission_values:
    - read
    - admin
  subject_match_any:
    user: permission.subject_value == principal.external_user_id
    department: permission.subject_value == principal.department
    role: permission.subject_value in principal.roles
```

重要推論限制：

- 「不同部門」不等於拒絕；無規則時預設開放，但仍受機密等級限制。
- 一旦某層存在規則，該層採 allow-list。
- 同部門文件在目前實作中會於文件 allow-list 之前直接允許。
- Session 文件不透過部門或知識庫分享。
- Permission API 全部為 admin-only。

## 4. 端點索引

權限標記：

- `public`：無 bearer token。
- `auth`：已驗證 principal。
- `admin`：principal.roles 包含 `admin`。
- `owner-or-admin`：Session 擁有者或 admin。
- `permission-filtered`：依第 3 節判斷。

### 4.1 Health

| Method | Path | Auth | Request | Response |
| --- | --- | --- | --- | --- |
| GET | `/health` | public | none | service status |
| GET | `/health/dependencies` | public | none | dependency status |

### 4.2 Chat

| Method | Path | Auth | Request | Response |
| --- | --- | --- | --- | --- |
| POST | `/chat/query` | auth + permission-filtered | `ChatQueryRequest` | `ChatQueryResponse` |
| POST | `/chat/stream` | auth + permission-filtered | `ChatQueryRequest` | SSE stream |
| POST | `/code-chat/query` | auth + permission-filtered | `CodeChatRequest` | `CodeChatResponse` |
| POST | `/code-chat/stream` | auth + permission-filtered | `CodeChatRequest` | SSE stream (`CodeChatResponse` in `done`) |
| GET | `/chat/sessions/{session_id}/messages` | owner-or-admin | path UUID | `ChatSessionMessages` |
| DELETE | `/chat/sessions/{session_id}` | owner-or-admin | path UUID | `ChatSessionDeleteResponse` |

```yaml
ChatQueryRequest:
  session_id: UUID|null = null
  knowledge_base_ids: list[UUID] = []
  query: string(min_length=1)
  top_k: integer(min=1,max=50,default=8)
  use_rerank: boolean = true
  use_masking: boolean = true
  use_tools: boolean = false

ChatQueryResponse:
  request_id: string
  session_id: UUID
  message_id: UUID
  answer: string  # only the main answer; structured headings are removed
  sections:
    key_points: list[string]
    sources: list[string]
    confidence: string|null
    limitations: list[string]
  usage:
    prompt_tokens: integer|null
    completion_tokens: integer|null
    total_tokens: integer|null
  citations: list[Citation]
  images: list[ImageReference]
  confidence: RiskLevel|string
  risk_level: RiskLevel
  masked_entities: list[MaskedEntity]
  tool_calls: list[ToolCallTrace]
```

`sections` is parsed from the provider's `### 2` through `### 5` output so the
frontend does not need to parse Markdown headings. `usage` is copied from the
OpenAI-compatible provider response. Token fields are `null` when the provider
does not return usage. When tool calling requires multiple completions, usage
is the sum of all completions in the request.

RAG mode selection:

```yaml
if knowledge_base_ids is non_empty:
  mode: knowledge_base_plus_same_session_rag
else_if session_has_any_document_record:
  mode: session_rag
else:
  mode: general_llm
  citations: []
  images: []
  prohibition: do not invent documents, citations, page numbers, or source URLs
```

If a document source exists but retrieval finds no relevant chunk, do not fall back to general knowledge.

### 4.3 Documents

| Method | Path | Auth | Request | Response |
| --- | --- | --- | --- | --- |
| GET | `/documents/formats` | auth | none | `DocumentFormatsResponse` |
| POST | `/documents/upload` | auth | multipart fields | `DocumentUploadResponse` |
| GET | `/documents` | auth + permission-filtered | query filters | `PageResponse[DocumentListItem]` |
| GET | `/documents/{document_id}/status` | auth + permission-filtered | path UUID | `DocumentStatusResponse` |
| GET | `/documents/{document_id}` | auth + permission-filtered | path UUID | `DocumentDetail` |
| POST | `/documents/{document_id}/reindex` | admin | path UUID | `ReindexResponse` |
| POST | `/documents/{document_id}/archive` | admin | path UUID | `DocumentArchiveResponse` |

Upload multipart contract:

```yaml
file: binary(required)
scope: knowledge_base|session(default=knowledge_base)
knowledge_base_id: UUID|null
session_id: UUID|null
confidential_level: ConfidentialLevel(required)
department: string|null
document_type: string|null
version: string|null

constraints:
  knowledge_base:
    knowledge_base_id: required
    session_id: forbidden
  session:
    knowledge_base_id: forbidden
    session_id: optional; server creates one when absent
```

List query:

```yaml
knowledge_base_id: UUID|null
status: string|null
confidential_level: ConfidentialLevel|null
page: integer(min=1,default=1)
page_size: integer(min=1,max=100,default=20)
```

### 4.4 Knowledge Bases

| Method | Path | Auth | Request | Response |
| --- | --- | --- | --- | --- |
| POST | `/knowledge-bases` | auth | `KnowledgeBaseCreate` | `KnowledgeBaseResponse` |
| GET | `/knowledge-bases` | auth + permission-filtered | none | `{"items": list[KnowledgeBaseResponse]}` |

```yaml
KnowledgeBaseCreate:
  name: string
  description: string|null
  owner_department: string|null
  default_confidential_level: ConfidentialLevel = internal
```

### 4.5 Skills

| Method | Path | Auth |
| --- | --- | --- |
| GET | `/skills` | auth + skill permission filter |
| POST | `/skills` | admin |
| GET | `/skills/{name}` | auth + skill permission |
| PATCH | `/skills/{name}` | admin |
| DELETE | `/skills/{name}` | admin |
| POST | `/skills/{name}/files` | admin; multipart `file`, `relative_path`, `overwrite=false` |
| GET | `/skills/{name}/files/{file_path:path}` | auth + skill permission |
| DELETE | `/skills/{name}/files/{file_path:path}` | admin |

No Skill rules means open to authenticated users; one or more rules means allow-list. System Skills cannot be deleted. `SKILL.md` changes must use the Skill update endpoint.

### 4.6 LLMWiki

All `knowledge_base_ids` parameters below are repeated query parameters of type `list[UUID]`, min length 1, and results are permission-filtered.

| Method | Path | Auth | Query |
| --- | --- | --- | --- |
| GET | `/llmwiki/search` | auth | `knowledge_base_ids`, `q=""`, `limit=10 (1..50)` |
| GET | `/llmwiki/index` | auth | `knowledge_base_ids` |
| GET | `/llmwiki/demo` | public | none |
| POST | `/llmwiki/topics/{topic}/compile` | auth | `knowledge_base_ids`, `top_k=24 (1..80)`, `include_graph=true` |
| GET | `/llmwiki/topics/{topic}` | auth | `knowledge_base_ids`, `top_k=12 (1..50)`, `include_graph=true`, `prefer_compiled=true` |
| GET | `/llmwiki/topics/{topic}/graph` | auth | `knowledge_base_ids`, `top_k=20 (1..80)` |
| GET | `/llmwiki/lint` | auth | `knowledge_base_ids` |
| GET | `/llmwiki/operations` | auth | `limit=20 (1..100)` |

Primary response models: `LLMWikiSearchResponse`, `LLMWikiTopicPage`, `LLMWikiCompileResponse`, `LLMWikiIndexResponse`, `LLMWikiGraph`, `LLMWikiLintResponse`, `LLMWikiOperationLogResponse`.

### 4.7 Permissions

All endpoints are admin-only.

| Method | Path | Request/Response |
| --- | --- | --- |
| POST | `/permissions/documents/{document_id}` | `DocumentPermissionCreate → DocumentPermissionResponse` |
| GET | `/permissions/documents/{document_id}` | `DocumentPermissionsResponse` |
| DELETE | `/permissions/documents/permissions/{permission_id}` | 204 |
| POST | `/permissions/knowledge-bases/{knowledge_base_id}` | `KnowledgeBasePermissionCreate → KnowledgeBasePermissionResponse` |
| GET | `/permissions/knowledge-bases/{knowledge_base_id}` | `KnowledgeBasePermissionsResponse` |
| DELETE | `/permissions/knowledge-bases/permissions/{permission_id}` | 204 |
| POST | `/permissions/skills/{skill_name}` | `SkillPermissionCreate → SkillPermissionResponse` |
| GET | `/permissions/skills/{skill_name}` | `SkillPermissionsResponse` |
| DELETE | `/permissions/skills/{skill_name}/{permission_id}` | 204 |

Permission mutation body:

```yaml
subject_type: user|role|department
subject_value: string
permission: read|write|admin
```

### 4.8 Audit

All endpoints are admin-only and return `{"items": [...]}`.

| Method | Path | Query |
| --- | --- | --- |
| GET | `/audit/chat-logs` | `user_id?`, `start_time?`, `end_time?`, `risk_level?` |
| GET | `/audit/masking-events` | `limit=100 (1..500)` |
| GET | `/audit/retrieval-logs` | `message_id?`, `document_id?`, `limit=100 (1..500)` |
| GET | `/audit/llm-logs` | `message_id?`, `status?`, `limit=100 (1..500)` |
| GET | `/audit/permission-denied` | `user_id?`, `limit=100 (1..500)` |

### 4.9 Admin

All endpoints are admin-only.

| Method | Path | Request | Response |
| --- | --- | --- | --- |
| GET | `/admin/status` | none | `{"status":"ok"}` |
| POST | `/admin/llm/test` | `LLMTestRequest` | `LLMTestResponse` |
| GET | `/admin/embedding/status` | none | `EmbeddingStatusResponse` |
| POST | `/admin/embedding/test` | `EmbeddingTestRequest` | `EmbeddingTestResponse` |
| GET | `/admin/operations/metrics` | none | `OperationMetricsResponse` |
| POST | `/admin/retention` | `RetentionPolicyRequest` | `RetentionPolicyResponse` |
| GET | `/admin/sensitive-rules` | none | `{"items": list[SensitiveRuleResponse]}` |
| POST | `/admin/sensitive-rules` | `SensitiveRuleRequest` | `SensitiveRuleResponse` |
| PATCH | `/admin/sensitive-rules/{rule_id}` | `{"is_active": boolean}` | `SensitiveRuleResponse` |
| POST | `/admin/sensitive-rules/templates` | none | `{"items": list[SensitiveRuleResponse]}` |

```yaml
LLMTestRequest:
  system_prompt: string(min=1,max=4000,default="You are a helpful software engineering assistant.")
  message: string(min=1,max=20000)

EmbeddingTestRequest:
  text: string(min=1,max=20000,default="LM Agent embedding connectivity test.")

RetentionPolicyRequest:
  apply: boolean = false
  document_archive_after_days: integer(min=1,default=365)
  chat_message_retention_days: integer(min=1,default=365)
  retrieval_log_retention_days: integer(min=1,default=90)
  masking_event_retention_days: integer(min=1,default=180)
  llm_log_retention_days: integer(min=1,default=180)
  audit_event_retention_days: integer(min=1,default=365)
```

## 5. 錯誤契約

Application `APIError` response:

```json
{
  "request_id": "string; empty when X-Request-ID absent",
  "error_code": "ErrorCode",
  "message": "human-readable string",
  "details": {}
}
```

HTTP mapping is route-dependent. Common mappings:

```yaml
401: UNAUTHORIZED
403: PERMISSION_DENIED
404:
  - DOCUMENT_NOT_FOUND
  - SKILL_NOT_FOUND
  - INVALID_REQUEST for selected missing session/permission resources
400:
  - INVALID_REQUEST
  - DLP_BLOCKED
500:
  - INTERNAL_ERROR
  - service-specific failures when wrapped
```

Do not assume every 404 uses a `*_NOT_FOUND` code. FastAPI/Pydantic validation failures may use framework-native 422 payloads rather than the application `APIError` envelope.

## 6. Agent 生成要求

When constructing requests:

```yaml
must:
  - read /openapi.json when live service is reachable
  - include Authorization for every non-public endpoint
  - use multipart/form-data for document and Skill file uploads
  - serialize UUID values as strings
  - repeat knowledge_base_ids query keys for list[UUID] LLMWiki inputs
  - poll document status until ready or failed before RAG use
  - preserve X-Request-ID across a logical operation when tracing is needed
must_not:
  - invent request fields
  - send knowledge_base_id together with scope=session
  - send session_id together with scope=knowledge_base
  - claim cross-department access is always denied
  - expose or infer documents removed by permission filtering
  - fabricate citations in general LLM mode
```

## 7. 程式碼對照

```yaml
application_factory: app/main.py
router_registry: app/api/v1/router.py
routes: app/api/v1/*.py
schemas: app/schemas/*.py
authentication: app/core/security.py
constants: app/core/constants.py
error_handlers: app/core/exceptions.py
permission_algorithm: app/services/permission_service.py
runtime_schema: GET /openapi.json
```
