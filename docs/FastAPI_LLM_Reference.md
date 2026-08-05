---
document_id: lm-agent-fastapi-llm-reference
document_type: api_contract_companion
language: zh-TW
api_name: LM Agent API
api_version: 0.13.0
base_path: /api/v1
branch: codex/session-analysis-workspace
updated_at: 2026-07-30
canonical_runtime_schema: GET /openapi.json
human_guide: docs/FastAPI_Human_Guide.md
frontend_upload_guide: docs/Frontend_File_Upload_Guide.md
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
  version: 0.13.0
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
RetrievalScope:
  - auto
  - attachments_only
  - session_attachments
  - knowledge_bases_only
  - session_and_knowledge_bases
AnalysisJobStatus:
  - queued
  - running
  - completed
  - failed
  - cancelled
AnalysisFileStatus:
  - profile_queued
  - profiling
  - ready
  - failed
AnalysisPlanDraftStatus:
  - validated
  - confirmed
  - rejected
WorkspaceVisibility:
  - private
  - shared
PermissionSubjectType:
  - user
  - role
  - department
  - project
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
  - ATTACHMENT_NOT_READY
  - ANALYSIS_NOT_FOUND
  - ANALYSIS_FAILED
  - SKILL_NOT_FOUND
  - DLP_BLOCKED
  - EMBEDDING_SERVICE_ERROR
  - LLM_SERVICE_ERROR
  - CHAT_BUSY
  - CHAT_TIMEOUT
  - PROMPT_TOO_LARGE
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
| GET | `/chat/sessions/{session_id}/attachments` | owner-or-admin | path UUID | `SessionAttachmentListResponse` |
| DELETE | `/chat/sessions/{session_id}/attachments/{document_id}` | owner-or-admin | path UUIDs | `SessionAttachmentDeleteResponse` |
| DELETE | `/chat/sessions/{session_id}` | owner-or-admin | path UUID | `ChatSessionDeleteResponse` |

```yaml
ChatQueryRequest:
  session_id: UUID|null = null
  knowledge_base_ids: list[UUID] (max_length=20) = []
  attachment_ids: list[UUID] (max_length=20) = []
  retrieval_scope: RetrievalScope = auto
  query: string(min_length=1)
  top_k: integer(min=1,max=50,default=8)
  use_rerank: boolean = true
  use_masking: boolean = true
  use_tools: boolean = false
  workspace_id: UUID|null

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

SessionAttachmentItem:
  document_id: UUID
  filename: string
  file_type: string
  status: string
  source_type: string
  page_count: integer|null
  chunk_count: integer
  created_at: datetime

SessionAttachmentListResponse:
  session_id: UUID
  items: list[SessionAttachmentItem]

SessionAttachmentDeleteResponse:
  session_id: UUID
  document_id: UUID
  deleted_files: integer
  status: deleted

ChatSessionDeleteResponse:
  session_id: UUID
  deleted_documents: integer
  deleted_analysis_files: integer
  deleted_messages: integer
  deleted_files: integer
  status: deleted
```

`sections` is parsed from the provider's `### 2` through `### 5` output so the
frontend does not need to parse Markdown headings. `usage` is copied from the
OpenAI-compatible provider response. Token fields are `null` when the provider
does not return usage. When tool calling requires multiple completions, usage
is the sum of all completions in the request.

Retrieval scope validation:

```yaml
attachment_ids:
  requires: session_id
attachments_only:
  requires:
    - session_id
    - non_empty attachment_ids
session_attachments:
  requires: session_id
knowledge_bases_only:
  requires: non_empty knowledge_base_ids
session_and_knowledge_bases:
  requires: session_id
attachment_constraints:
  - every attachment belongs to session_id
  - every attachment is ready
  - caller owns the session or is admin
auto:
  with_attachment_ids: selected attachments only
  otherwise: legacy same-session and/or knowledge-base behavior
```

If no source is selected and the Session has no document, Chat may use general LLM mode with
empty citations and images. If a document source exists but retrieval finds no relevant chunk,
do not fall back to general knowledge.

### 4.3 Documents

| Method | Path | Auth | Request | Response |
| --- | --- | --- | --- | --- |
| GET | `/documents/formats` | auth | none | `DocumentFormatsResponse` |
| POST | `/documents/upload` | auth | multipart fields | `DocumentUploadResponse` |
| GET | `/documents` | auth + permission-filtered | query filters | `PageResponse[DocumentListItem]` |
| GET | `/documents/{document_id}/status` | auth + permission-filtered | path UUID | `DocumentStatusResponse` |
| GET | `/documents/{document_id}` | auth + permission-filtered | path UUID | `DocumentDetail` |
| PATCH | `/documents/{document_id}` | auth + write permission | `DocumentUpdate` | `DocumentDetail` |
| POST | `/documents/{document_id}/reindex` | auth + write permission | path UUID | `ReindexResponse` |
| POST | `/documents/{document_id}/archive` | auth + write permission | path UUID | `DocumentArchiveResponse` |
| DELETE | `/documents/{document_id}` | auth + manage permission | path UUID | 204 |

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

### 4.4 Workspace and Analysis

Analysis resources are Workspace-scoped. Sessions are optional interaction origins.
Access uses Workspace `read`/`write`/`admin` permissions plus file confidentiality clearance.
Analysis files are not documents, do not create chunks or embeddings, and are not searchable
through Chat retrieval.

| Method | Path | Auth | Request | Response |
| --- | --- | --- | --- | --- |
| POST | `/workspaces` | authenticated | `WorkspaceCreate` | `WorkspaceResponse` |
| GET | `/workspaces` | authenticated | none | `WorkspaceListResponse` |
| GET | `/workspaces/{workspace_id}` | workspace-read | path UUID | `WorkspaceResponse` |
| PATCH | `/workspaces/{workspace_id}` | workspace-admin | `WorkspaceUpdate` | `WorkspaceResponse` |
| DELETE | `/workspaces/{workspace_id}` | workspace-admin | path UUID | 204 |
| PUT | `/workspaces/{workspace_id}/sessions/{session_id}` | workspace-read + session-owner | path UUIDs | `WorkspaceSessionLinkResponse` |
| DELETE | `/workspaces/{workspace_id}/sessions/{session_id}` | workspace-read + session-owner | path UUIDs | `WorkspaceSessionLinkResponse` |
| POST | `/workspaces/{workspace_id}/permissions` | workspace-admin | `WorkspacePermissionCreate` | `WorkspacePermissionResponse` |
| GET | `/workspaces/{workspace_id}/permissions` | workspace-admin | path UUID | `WorkspacePermissionsResponse` |
| DELETE | `/workspaces/{workspace_id}/permissions/{permission_id}` | workspace-admin | path UUIDs | 204 |
| GET | `/workspaces/{workspace_id}/artifacts` | workspace-read | path UUID | `AnalysisArtifactListResponse` |
| GET | `/workspaces/{workspace_id}/artifacts/{artifact_id}` | workspace-read | path UUIDs | `AnalysisArtifactResponse` |
| GET | `/workspaces/{workspace_id}/artifacts/{artifact_id}/download` | workspace-read | path UUIDs | file stream |
| POST | `/analysis/files/upload` | workspace-write | multipart fields | `AnalysisFileUploadResponse` |
| GET | `/analysis/files` | workspace-read | `workspace_id` or compatible `session_id` | `AnalysisFileListResponse` |
| GET | `/analysis/files/{file_id}` | workspace-read | path UUID | `AnalysisFileItem` |
| GET | `/analysis/files/{file_id}/inspect` | workspace-read | path UUID | `SpreadsheetInspectionResponse` |
| POST | `/analysis/files/{file_id}/profile/retry` | workspace-write | path UUID | `AnalysisFileUploadResponse`, HTTP 202 |
| DELETE | `/analysis/files/{file_id}` | workspace-write | path UUID | 204 |
| GET | `/analysis/recipes` | authenticated | none | `RecipeListResponse` |
| GET | `/analysis/recipes/{recipe_id}` | authenticated | `version=1.0` | `RecipeDefinition` |
| POST | `/analysis/intents/validate` | workspace-read | `AnalysisIntentValidateRequest` | `AnalysisIntentValidationResponse` |
| GET | `/analysis/metrics/recipes` | workspace-read | `workspace_id` | `RecipeMetricsResponse` |
| POST | `/analysis/plans/validate` | workspace-read | `AnalysisPlanValidateRequest` | `AnalysisPlanValidationResponse` |
| POST | `/analysis/plan-drafts` | workspace-write + LLM | `AnalysisPlanDraftCreate` | `AnalysisPlanDraftResponse`, HTTP 201 |
| GET | `/analysis/plan-drafts/{draft_id}` | workspace-read | path UUID | `AnalysisPlanDraftResponse` |
| POST | `/analysis/plan-drafts/{draft_id}/confirm` | workspace-write | `AnalysisPlanDraftConfirmRequest` | `AnalysisJobResponse`, HTTP 202 |
| POST | `/analysis/jobs` | workspace-write | `AnalysisJobCreate` | `AnalysisJobResponse`, HTTP 202 |
| GET | `/analysis/jobs` | workspace-read | query filters | `AnalysisJobListResponse` |
| GET | `/analysis/jobs/{job_id}` | workspace-read | path UUID | `AnalysisJobResponse` |
| POST | `/analysis/jobs/{job_id}/cancel` | workspace-write | path UUID | `AnalysisJobResponse` |
| POST | `/analysis/jobs/{job_id}/retry` | workspace-write | path UUID | `AnalysisJobResponse`, HTTP 202 |
| POST | `/analysis/jobs/{job_id}/explain` | workspace-read | path UUID | `AnalysisExplanationResponse` |
| POST | `/analysis/hybrid-answer` | workspace-read; workspace-write when `create_report=true` | `AnalysisHybridRequest` | `AnalysisHybridResponse` |
| POST | `/analysis/jobs/{job_id}/export` | workspace-write | `AnalysisExportRequest` | `AnalysisArtifactCreatedResponse`, HTTP 201 |
| POST | `/analysis/jobs/{job_id}/reports` | workspace-write | `AnalysisReportRequest` | `AnalysisArtifactCreatedResponse`, HTTP 201 |
| POST | `/analysis/jobs/{job_id}/charts` | workspace-write | `AnalysisChartArtifactRequest` | `AnalysisArtifactCreatedResponse`, HTTP 201 |

```yaml
WorkspaceCreate:
  name: string(min=1,max=255)
  description: string|null(max=4000)
  visibility: private|shared = private

WorkspaceResponse:
  workspace_id: UUID
  owner_user_id: UUID
  name: string
  description: string|null
  visibility: private|shared
  is_personal: boolean
  is_active: boolean
  permission: read|write|admin
  session_count: integer
  file_count: integer
  job_count: integer
  created_at: datetime
  updated_at: datetime

WorkspacePermissionCreate:
  subject_type: user|role|department|project
  subject_value: string(min=1,max=255)
  permission: read|write|admin

AnalysisFileUploadMultipart:
  file: binary(required; same formats as document upload)
  session_id: UUID|null
  workspace_id: UUID|null
  confidential_level: ConfidentialLevel = internal
  constraints:
    - one of session_id or workspace_id is required
    - when session_id is supplied, the backend resolves or links its Workspace
    - when session_id is omitted, workspace_id is required and workspace-write is checked
    - xlsx is opened read-only through Excel COM before any OpenXML parser
    - Office COM failure is returned later through profile_error with an OFFICE_* code

AnalysisFileUploadResponse:
  file_id: UUID
  workspace_id: UUID
  representation_format: string|null  # markdown | markdown_table | markdown_ocr
  llm_readable: boolean
  analysis_ready: boolean  # true only for XLSX/CSV with Parquet datasets
  session_id: UUID|null
  filename: string
  file_type: pdf|image|office|text|structured|code extension
  size_bytes: integer
  status: profile_queued|profiling|ready|failed
  profile_progress: integer(0..100)
  profile_error: string|null
  dataset_count: integer
  profiled_at: datetime|null
  expires_at: datetime|null

SpreadsheetInspectionResponse:
  file_id: UUID
  workspace_id: UUID
  filename: string
  file_type: string
  sheets:
    - name: string
      row_count: integer|null
      column_count: integer
      columns:
        - name: string
          inferred_type: string
          sample_values: list[scalar]
          null_count_in_sample: integer
      sample_rows: list[object]
      formula_cells_in_sample: integer
      formatted_cells_in_sample: integer
      warnings: list[string]
  warnings: list[string]

FilterCondition:
  column: string
  operator: eq|ne|gt|gte|lt|lte|contains|in|is_null|not_null
  value: scalar|list[scalar]|null

AggregationSpec:
  function: count|distinct_count|sum|mean|std|min|max|count_if|percentile
  column: string|null
  alias: string(min=1,max=128)
  condition: FilterCondition|null
  percentile: number(0..1)|null
  constraints:
    - column is required except for count and count_if
    - condition is required for count_if
    - percentile is required when function=percentile

AnalysisPlan:
  sheet: string|null
  sources:
    - file_id: UUID
      alias: string(regex="^[A-Za-z_][A-Za-z0-9_]{0,63}$")
      sheet: string|null
    max_length: 8
  joins:
    - left_alias: string
      right_alias: string
      left_on: list[string] (min=1,max=10)
      right_on: list[string] (min=1,max=10)
      how: inner|left|right|full = inner
    max_length: 7
  select: list[string] (max_length=50) = []
  group_by: list[string] (max_length=5) = []
  filters: list[FilterCondition] (max_length=20) = []
  aggregations: list[AggregationSpec] (max_length=20) = []
  date_buckets:
    - column: string
      unit: day|week|month|quarter|year
      alias: string(min=1,max=128)
    max_length: 5
  pivot:
    index: list[string] (min=1,max=5)
    columns: string
    values: string
    aggregation: count|sum|mean|min|max = sum
  correlation:
    columns: list[string] (min=2,max=20)
    method: pearson|spearman = pearson
  sort:
    - column: string
      direction: asc|desc
    max_length: 5
  limit: integer(min=1,max=5000,default=1000)
  charts:
    - type: bar|line|scatter
      x_field: string
      y_field: string
      title: string(max=255,default="Analysis result")
      series_field: string|null
      x_type: category|value|time = category
      y_unit: string|null(max=32)
      decimal_places: integer(0..10,default=2)
      tooltip_fields: list[string] (max_length=20)
      zoom: boolean = false
    max_length: 5
  constraints:
    - select, aggregations, pivot, or correlation is required
    - raw-row sort without aggregations is rejected
    - output fields used by sort and charts must exist
    - all sources must be ready and belong to the same Workspace
    - every source after the first must be connected exactly once by an ordered Join
    - join aliases and source aliases must exist and be unique
    - aggregation aliases must be unique and must not collide with group_by names;
      backend rejects duplicate aliases and aliases that collide with group_by columns
    - date bucket aliases must be unique and must not collide with aggregation aliases
    - pivot and correlation cannot be requested together

AnalysisPlanValidateRequest:
  file_id: UUID
  plan: AnalysisPlan

AnalysisPlanValidationResponse:
  valid: true
  normalized_plan: AnalysisPlan
  warnings: list[string]

AnalysisJobCreate:
  file_id: UUID
  session_id: UUID|null
  plan: AnalysisPlan

AnalysisJobResponse:
  job_id: UUID
  workspace_id: UUID
  session_id: UUID|null
  file_id: UUID
  status: queued|running|completed|failed|cancelled
  plan: AnalysisPlan
  progress: integer(0..100)
  result: object|null
  error_message: string|null
  retry_of_job_id: UUID|null
  draft_id: UUID|null
  cancel_requested_at: datetime|null
  created_at: datetime
  updated_at: datetime
  finished_at: datetime|null

AnalysisJobListQuery:
  workspace_id: UUID|null
  session_id: UUID|null
  file_id: UUID|null
  status: AnalysisJobStatus|null
  page: integer(min=1,default=1)
  page_size: integer(min=1,max=200,default=50)
  constraints:
    - workspace_id or session_id is required

AnalysisPlanDraftCreate:
  workspace_id: UUID
  question: string(min=2,max=4000)
  file_ids: list[UUID] (min=1,max=8)
  session_id: UUID|null

AnalysisPlanDraftResponse:
  draft_id: UUID
  workspace_id: UUID
  session_id: UUID|null
  question: string
  file_ids: list[UUID]
  plan: AnalysisPlan
  warnings: list[string]
  status: validated|confirmed|rejected
  confirmation_required: true
  usage: LLMUsage
  created_at: datetime
  confirmed_at: datetime|null

AnalysisPlanDraftConfirmRequest:
  plan: AnalysisPlan|null
  behavior:
    - null confirms the validated draft plan
    - a supplied edited plan is validated again before a Job is queued

AnalysisResult:
  summary:
    processed_rows: integer
    matched_rows: integer
    result_rows: integer
    returned_rows: integer
    truncated: boolean
    warnings: list[string]
    query_engine: string|null
  table:
    columns: list[{key: string, label: string}]
    rows: list[object]
  charts:
    - schema_version: "1.0"|"2.0"
      type: bar|line|scatter
      title: string
      x_field: string
      y_field: string
      series_field: string|null
      x_type: category|value|time
      y_unit: string|null
      decimal_places: integer
      tooltip_fields: list[string]
      zoom: boolean
      data: list[object]
  plan: AnalysisPlan

AnalysisExplanationResponse:
  job_id: UUID
  answer: string
  usage: LLMUsage

AnalysisHybridRequest:
  workspace_id: UUID
  question: string(min=2,max=4000)
  analysis_job_ids: list[UUID] (min=1,max=10)
  knowledge_base_ids: list[UUID] (max=20) = []
  top_k: integer(min=1,max=30,default=8)
  use_rerank: boolean = true
  create_report: boolean = true

AnalysisHybridResponse:
  workspace_id: UUID
  answer: string
  analysis_job_ids: list[UUID]
  citations: list[Citation]
  report_artifact_id: UUID|null
  usage: LLMUsage

AnalysisExportRequest:
  format: csv|json|parquet = csv
  filename: string|null(max=255)

AnalysisReportRequest:
  title: string(min=1,max=255,default="Analysis report")
  include_result_rows: boolean = true
  max_rows: integer(min=1,max=5000,default=200)

AnalysisChartArtifactRequest:
  chart_index: integer(min=0,default=0)
  filename: string|null(max=255)

AnalysisArtifactCreatedResponse:
  artifact_id: UUID
  workspace_id: UUID
  job_id: UUID
  artifact_type: string
  filename: string
  mime_type: string
  size_bytes: integer

AnalysisArtifactResponse:
  artifact_id: UUID
  workspace_id: UUID
  file_id: UUID|null
  job_id: UUID|null
  artifact_type: string
  filename: string
  mime_type: string|null
  size_bytes: integer
  metadata: object
  created_at: datetime
```

Defaults: upload 500 MB, scan 5,000,000 rows, 5,000 groups, return 5,000 rows,
50 background profile samples (20 for legacy synchronous inspect), 3,600-second
profile timeout, 1,800-second Job timeout,
500,000 advanced collect rows, 5,000 chart points, and 100,000 export rows.
Workspace files are persistent by default; `ANALYSIS_RETENTION_HOURS=168` applies
only to legacy rows that have an explicit expiry. The dedicated worker command is
`python -m app.workers.analysis_tasks`.

Execution boundary:

```yaml
natural_language_path:
  - POST /analysis/plan-drafts
  - backend validates model JSON against actual Parquet schemas
  - user reviews the normalized plan
  - POST /analysis/plan-drafts/{draft_id}/confirm
  - confirm creates exactly one queued Job
direct_plan_path:
  - POST /analysis/plans/validate
  - POST /analysis/jobs with normalized_plan
forbidden:
  - model-generated Python execution
  - model-generated SQL execution
  - model-generated JavaScript execution
  - user-supplied filesystem or Parquet paths
```

### 4.5 Knowledge Bases

| Method | Path | Auth | Request | Response |
| --- | --- | --- | --- | --- |
| POST | `/knowledge-bases` | auth | `KnowledgeBaseCreate` | `KnowledgeBaseResponse` |
| GET | `/knowledge-bases` | auth + permission-filtered | none | `{"items": list[KnowledgeBaseResponse]}` |
| GET | `/knowledge-bases/{knowledge_base_id}` | auth + permission-filtered | path UUID | `KnowledgeBaseResponse` |
| PATCH | `/knowledge-bases/{knowledge_base_id}` | auth + admin permission | `KnowledgeBaseUpdate` | `KnowledgeBaseResponse` |
| DELETE | `/knowledge-bases/{knowledge_base_id}` | auth + admin permission | path UUID | 204 |

```yaml
KnowledgeBaseCreate:
  name: string
  description: string|null
  owner_department: string|null
  default_confidential_level: ConfidentialLevel = internal
```

### 4.6 Skills

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

### 4.7 LLMWiki

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

### 4.8 Permissions

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

### 4.9 Audit

All endpoints are admin-only and return `{"items": [...]}`.

| Method | Path | Query |
| --- | --- | --- |
| GET | `/audit/chat-logs` | `user_id?`, `start_time?`, `end_time?`, `risk_level?` |
| GET | `/audit/masking-events` | `limit=100 (1..500)` |
| GET | `/audit/retrieval-logs` | `message_id?`, `document_id?`, `limit=100 (1..500)` |
| GET | `/audit/llm-logs` | `message_id?`, `status?`, `limit=100 (1..500)` |
| GET | `/audit/permission-denied` | `user_id?`, `limit=100 (1..500)` |

### 4.10 Admin

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
  - ANALYSIS_NOT_FOUND
  - SKILL_NOT_FOUND
  - INVALID_REQUEST for selected missing session/permission resources
409:
  - DOCUMENT_NOT_READY for spreadsheet profiling or unfinished analysis prerequisites
  - INVALID_REQUEST for illegal Job state transitions or Session/Workspace conflicts
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
  - use multipart/form-data for analysis file uploads
  - serialize UUID values as strings
  - repeat knowledge_base_ids query keys for list[UUID] LLMWiki inputs
  - poll document status until ready or failed before RAG use
  - poll analysis file status until ready or failed before inspect, validate, or Job creation
  - validate AnalysisPlan before creating an analysis job
  - show and explicitly confirm a natural-language AnalysisPlanDraft before execution
  - treat draft confirmation as Job creation; do not create the same Job again
  - poll analysis job status until completed, failed, or cancelled before reading result
  - use workspace_id when an analysis upload has no session_id
  - preserve X-Request-ID across a logical operation when tracing is needed
must_not:
  - invent request fields
  - send knowledge_base_id together with scope=session
  - send session_id together with scope=knowledge_base
  - send unready attachment IDs to Chat
  - treat analysis uploads as RAG documents
  - execute model-generated Python, SQL, or JavaScript for spreadsheet analysis
  - send filesystem or Parquet paths in AnalysisPlan
  - use analysis sources from different Workspaces
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
# Analysis IntentDraft contract

For natural-language spreadsheet analysis, prefer the versioned Recipe contract:

```json
{
  "schema_version": "1.0",
  "sources": [
    {
      "file_id": "approved UUID",
      "alias": "data",
      "sheet": "Production"
    }
  ],
  "recipe_id": "histogram",
  "recipe_version": "1.0",
  "inputs": {
    "source_field": "Thickness",
    "bin_width": 10
  },
  "filters": [],
  "chart_enabled": true,
  "title": "Thickness distribution"
}
```

Only use Recipe IDs and parameters returned by
`GET /api/v1/analysis/recipes`. Do not emit `x_field`, `y_field`, derived output
columns, Python, SQL, JavaScript, ECharts options, or file paths. The backend compiler
validates all file/sheet/column references, fixes only deterministic aliases and numeric
strings, and builds the result and chart contracts after execution.

When a column name is duplicated, use the supplied `column_id` or
`source_alias.column_name`. Never guess between candidates.

Phase 5 quality Recipe IDs are `descriptive_statistics`, `boxplot_summary`,
`outlier_iqr`, `correlation_matrix`, `yield_summary`, `spec_judgement`,
`process_capability`, and `control_chart`. Preserve backend results and warnings
verbatim: correlation is not causation; capability uses the returned sigma
definitions; control chart type and subgroup assumptions must not be changed by the
LLM.

When `ANALYSIS_INTENT_FLOW_ENABLED=false`, natural-language planning uses the legacy
AnalysisPlan contract. When `ANALYSIS_RECIPES_ENABLED=false`, do not retry Recipe
requests; use the still-supported legacy plan route only when it can represent the
requested operation.
