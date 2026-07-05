

# 文件二：FastAPI API 規格書

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
| file               | file   | 是  | PDF 或圖片                      |
| knowledge_base_id  | string | 是  | 知識庫 ID                       |
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
  "message": "Document uploaded successfully."
}
```

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
  "knowledge_base_id": "kb-001",
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

## 10. Error Response 格式

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