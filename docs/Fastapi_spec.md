

# 文件二：FastAPI API 規格書

> LLM / agent 讀取入口：本文件描述語意與生命週期；執行時的機器可讀 schema 為
> `GET /openapi.json`。所有 UUID 均使用 RFC 4122 字串，所有未列出的欄位都應視為不支援。

## 1. API 設計原則

1. API version 使用 `/api/v1`。
2. 使用 JSON 作為主要資料格式。
3. 文件上傳使用 `multipart/form-data`。
4. 後端從 token 或 header 解析使用者資訊，不應完全相信 request body 的 user_id。
5. 所有問答請求都必須寫入 audit log。
6. 所有文件檢索都必須經過權限檢查。
7. 所有 LLM 請求前都必須通過 DLP / masking。
8. API 回應需包含 request_id，方便追蹤問題。

---

## 2. API 分類

| 分類              | Prefix                    | 說明     |
| --------------- | ------------------------- | ------ |
| Health          | `/api/v1/health`          | 系統健康檢查 |
| Chat            | `/api/v1/chat`            | 問答相關   |
| Documents       | `/api/v1/documents`       | 文件管理   |
| Knowledge Bases | `/api/v1/knowledge-bases` | 知識庫管理  |
| Permissions     | `/api/v1/permissions`     | 權限設定   |
| Audit           | `/api/v1/audit`           | 稽核紀錄   |
| Admin           | `/api/v1/admin`           | 系統管理   |

---

## 3. 共用 Header

```http
Authorization: Bearer <token>
X-Request-ID: <uuid>
```

若前端使用內部 token，後端需驗證 token signature 或與前端 auth service 交換使用者資訊。

---

## 4. Health API

### 4.1 Health Check

```http
GET /api/v1/health
```

Response:

```json
{
  "status": "ok",
  "service": "ai-agent-api",
  "version": "0.1.0"
}
```

### 4.2 Dependency Check

```http
GET /api/v1/health/dependencies
```

Response:

```json
{
  "status": "ok",
  "postgres": "ok",
  "redis": "ok",
  "embedding_service": "ok",
  "llm_service": "ok"
}
```

---

## 5. Document API

### 5.0 查詢支援格式

```http
GET /api/v1/documents/formats
Authorization: Bearer <token>
```

Response 中的 `items` 是唯一格式清單來源，前端使用 `accept` 設定檔案選擇器：

```json
{
  "items": [
    {"extension": ".pdf", "file_type": "pdf", "category": "pdf"},
    {"extension": ".docx", "file_type": "docx", "category": "office"},
    {"extension": ".md", "file_type": "md", "category": "text"},
    {"extension": ".json", "file_type": "json", "category": "structured"}
  ],
  "accept": ".pdf,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp,.docx,.xlsx,.pptx,.txt,.md,.markdown,.log,.csv,.json,.yaml,.yml,.html,.htm,.xml"
}
```

新增格式的位置為 `app/utils/file_utils.py::UPLOAD_TYPE_GROUPS`；新類別還必須在
`DocumentIngestionService._process_document()` 註冊對應 parser。文字與結構化格式由
`app/services/text_document_parser_service.py` 處理。

---

### 5.1 上傳文件

```http
POST /api/v1/documents/upload
```

Content-Type:

```text
multipart/form-data
```

Request fields:

| 欄位                 | 型別     | 必填 | 說明                           |
| ------------------ | ------ | -- | ---------------------------- |
| file               | file   | 是  | 以 `GET /documents/formats` 回傳清單為準 |
| scope              | string | 否  | `knowledge_base`（預設）或 `session` |
| knowledge_base_id  | UUID   | 條件式 | `scope=knowledge_base` 時必填      |
| session_id         | UUID   | 否  | `scope=session` 時可填；省略則建立新 Session |
| confidential_level | string | 是  | 文件分級                         |
| department         | string | 否  | 所屬部門                         |
| document_type      | string | 否  | paper / report / sop / image |
| version            | string | 否  | 文件版本                         |

Response:

```json
{
  "request_id": "req-001",
  "document_id": "doc-001",
  "status": "uploaded",
  "scope": "session",
  "knowledge_base_id": null,
  "session_id": "2ecaed2e-49e5-4b95-b14c-438ef177b233",
  "message": "Document uploaded and queued for background processing."
}
```

Scope 規則（互斥）：

- `knowledge_base`：必須傳 `knowledge_base_id`，不得傳 `session_id`。
- `session`：不得傳 `knowledge_base_id`；`session_id` 省略時由伺服器建立並回傳。
- Session 文件不會出現在任何知識庫或 LLMWiki 中，只能由相同 `session_id` 的 Chat 請求檢索。
- 一般使用者只能使用自己擁有的 Session；管理員可維運所有 Session。

---

### 5.2 查詢文件列表

```http
GET /api/v1/documents
```

Query Parameters:

| 參數                 | 型別     | 必填 | 說明     |
| ------------------ | ------ | -- | ------ |
| knowledge_base_id  | string | 否  | 知識庫 ID |
| status             | string | 否  | 文件狀態   |
| confidential_level | string | 否  | 文件分級   |
| page               | int    | 否  | 頁碼     |
| page_size          | int    | 否  | 每頁筆數   |

Response:

```json
{
  "items": [
    {
      "document_id": "doc-001",
      "filename": "paper.pdf",
      "title": "Defect Detection Review",
      "status": "ready",
      "confidential_level": "internal",
      "page_count": 12,
      "created_at": "2026-07-04T10:00:00"
    }
  ],
  "page": 1,
  "page_size": 20,
  "total": 1
}
```

---

### 5.3 查詢文件狀態

```http
GET /api/v1/documents/{document_id}/status
```

Response:

```json
{
  "document_id": "doc-001",
  "status": "embedding",
  "progress": 72,
  "message": "Embedding chunks 120/168"
}
```

---

### 5.4 查詢文件詳細資訊

```http
GET /api/v1/documents/{document_id}
```

Response:

```json
{
  "document_id": "doc-001",
  "filename": "paper.pdf",
  "title": "Defect Detection Review",
  "scope": "knowledge_base",
  "knowledge_base_id": "kb-001",
  "session_id": null,
  "language": "en",
  "confidential_level": "internal",
  "department": "R&D",
  "status": "ready",
  "page_count": 12,
  "chunk_count": 85,
  "created_at": "2026-07-04T10:00:00"
}
```

---

### 5.5 重新索引文件

```http
POST /api/v1/documents/{document_id}/reindex
```

Response:

```json
{
  "request_id": "req-002",
  "document_id": "doc-001",
  "status": "queued",
  "message": "Reindex task has been queued."
}
```

---

### 5.6 封存文件

```http
POST /api/v1/documents/{document_id}/archive
```

Response:

```json
{
  "document_id": "doc-001",
  "status": "archived"
}
```

---

## 6. Knowledge Base API

### 6.1 建立知識庫

```http
POST /api/v1/knowledge-bases
```

Request:

```json
{
  "name": "Literature Knowledge Base",
  "description": "Factory literature and research papers",
  "owner_department": "R&D",
  "default_confidential_level": "internal"
}
```

Response:

```json
{
  "knowledge_base_id": "kb-001",
  "name": "Literature Knowledge Base",
  "is_active": true
}
```

---

### 6.2 查詢知識庫列表

```http
GET /api/v1/knowledge-bases
```

Response:

```json
{
  "items": [
    {
      "knowledge_base_id": "kb-001",
      "name": "Literature Knowledge Base",
      "owner_department": "R&D",
      "default_confidential_level": "internal",
      "document_count": 700,
      "is_active": true
    }
  ]
}
```

---

## 7. Chat API

### 7.1 一般問答

```http
POST /api/v1/chat/query
```

Request:

```json
{
  "session_id": "session-001",
  "knowledge_base_ids": ["kb-001"],
  "query": "Please summarize the main defect detection methods discussed in the papers.",
  "top_k": 8,
  "use_rerank": true,
  "use_masking": true
}
```

`knowledge_base_ids` 可省略或傳空陣列。檢索來源為指定知識庫，加上 `session_id`
所綁定的 Session 文件；若只需要暫存文件，請傳空陣列：

```json
{
  "session_id": "2ecaed2e-49e5-4b95-b14c-438ef177b233",
  "knowledge_base_ids": [],
  "query": "摘要我剛才上傳的文件",
  "top_k": 8,
  "use_rerank": true,
  "use_masking": true,
  "use_tools": false
}
```

Response:

```json
{
  "request_id": "req-003",
  "session_id": "session-001",
  "message_id": "msg-001",
  "answer": "The retrieved papers mainly discuss traditional image processing, deep learning-based detection, and hybrid inspection methods...",
  "citations": [
    {
      "document_id": "doc-001",
      "chunk_id": "chunk-001",
      "title": "Defect Detection Review",
      "page_start": 3,
      "page_end": 4,
      "section_title": "Methods",
      "score": 0.87
    }
  ],
  "confidence": "medium",
  "risk_level": "low",
  "masked_entities": [
    {
      "entity_type": "customer",
      "masked_value": "[CUSTOMER_001]"
    }
  ]
}
```

---

### 7.2 串流問答，預留接口

```http
POST /api/v1/chat/stream
```

MVP 可先不實作，或先保留路由。

---

### 7.3 查詢對話紀錄

```http
GET /api/v1/chat/sessions/{session_id}/messages
```

Response:

```json
{
  "session_id": "session-001",
  "messages": [
    {
      "message_id": "msg-001",
      "role": "user",
      "content": "Please summarize...",
      "created_at": "2026-07-04T10:00:00"
    },
    {
      "message_id": "msg-002",
      "role": "assistant",
      "content": "The retrieved papers mainly discuss...",
      "created_at": "2026-07-04T10:00:10"
    }
  ]
}
```

---

### 7.4 刪除 Session

```http
DELETE /api/v1/chat/sessions/{session_id}
Authorization: Bearer <token>
```

刪除範圍包含該 Session 的聊天訊息、retrieval/LLM/masking 紀錄、處理工作、
Session 文件、切片、圖片、原始檔與 Markdown。知識庫文件不受影響。操作不可復原。

Response:

```json
{
  "session_id": "2ecaed2e-49e5-4b95-b14c-438ef177b233",
  "deleted_documents": 1,
  "deleted_messages": 4,
  "deleted_files": 3,
  "status": "deleted"
}
```

錯誤：不存在回傳 `404 INVALID_REQUEST`；非擁有者回傳 `403 PERMISSION_DENIED`。

---

## 8. Permission API

### 8.1 設定文件權限

```http
POST /api/v1/permissions/documents/{document_id}
```

Request:

```json
{
  "subject_type": "department",
  "subject_value": "R&D",
  "permission": "read"
}
```

Response:

```json
{
  "permission_id": "perm-001",
  "document_id": "doc-001",
  "subject_type": "department",
  "subject_value": "R&D",
  "permission": "read"
}
```

---

### 8.2 查詢文件權限

```http
GET /api/v1/permissions/documents/{document_id}
```

Response:

```json
{
  "document_id": "doc-001",
  "permissions": [
    {
      "subject_type": "department",
      "subject_value": "R&D",
      "permission": "read"
    }
  ]
}
```

---

## 9. Audit API

### 9.1 查詢問答紀錄

```http
GET /api/v1/audit/chat-logs
```

Query Parameters:

| 參數         | 說明   |
| ---------- | ---- |
| user_id    | 使用者  |
| start_time | 起始時間 |
| end_time   | 結束時間 |
| risk_level | 風險等級 |

Response:

```json
{
  "items": [
    {
      "message_id": "msg-001",
      "user_id": "user-001",
      "query": "Please summarize...",
      "risk_level": "low",
      "created_at": "2026-07-04T10:00:00"
    }
  ]
}
```

---

### 9.2 查詢遮罩事件

```http
GET /api/v1/audit/masking-events
```

Response:

```json
{
  "items": [
    {
      "event_id": "mask-001",
      "message_id": "msg-001",
      "entity_type": "customer",
      "masked_value": "[CUSTOMER_001]",
      "masking_method": "dictionary",
      "risk_level": "medium",
      "created_at": "2026-07-04T10:00:00"
    }
  ]
}
```

---

## 10. Skills API

```http
GET    /api/v1/skills
POST   /api/v1/skills
GET    /api/v1/skills/{name}
PATCH  /api/v1/skills/{name}
DELETE /api/v1/skills/{name}
POST   /api/v1/skills/{name}/files
GET    /api/v1/skills/{name}/files/{file_path}
DELETE /api/v1/skills/{name}/files/{file_path}
GET    /api/v1/permissions/skills/{name}
POST   /api/v1/permissions/skills/{name}
DELETE /api/v1/permissions/skills/{name}/{permission_id}
```

Skill access rules support `user`, `department`, and `role` subjects with `read` or `admin`
permission. No rules means open authenticated access; one or more rules changes the Skill to an
allow list. Unauthorized Skills are omitted from lists and LLM prompt resolution, while direct
metadata and raw-file requests return `403`. Skill and permission mutations require the `admin`
role. Each file record contains an authenticated `raw_url` for inline frontend preview. System
Skills cannot be deleted, and `SKILL.md` must be changed through the Skill update endpoint.

---

## 10.1 Embedding Service 管理 API

```http
GET /api/v1/admin/embedding/status
POST /api/v1/admin/embedding/test
Authorization: Bearer <admin-token>
```

狀態端點代理獨立服務的 `GET /status`，回傳模型載入狀態、device、維度、port、
請求量、錯誤量及延遲。測試端點請求：

```json
{"text": "AOI defect inspection / 自動光學檢測"}
```

回應包含 `configured_dimension`、`actual_dimension`、`latency_ms`、
`vector_norm` 與前 8 個向量值。兩個端點都不建立資料庫 Session。

---

## 11. Error Response 格式

所有錯誤統一格式：

```json
{
  "request_id": "req-001",
  "error_code": "PERMISSION_DENIED",
  "message": "User does not have permission to access this document.",
  "details": {}
}
```

常見錯誤碼：

| Error Code              | 說明             |
| ----------------------- | -------------- |
| INVALID_REQUEST         | 請求格式錯誤         |
| UNAUTHORIZED            | 未登入或 token 無效  |
| PERMISSION_DENIED       | 權限不足           |
| DOCUMENT_NOT_FOUND      | 找不到文件          |
| DOCUMENT_NOT_READY      | 文件尚未完成索引       |
| DLP_BLOCKED             | 命中高風險機密規則      |
| EMBEDDING_SERVICE_ERROR | Embedding 服務錯誤 |
| LLM_SERVICE_ERROR       | LLM API 錯誤     |
| INTERNAL_ERROR          | 系統內部錯誤         |

---
