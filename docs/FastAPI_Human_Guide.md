# LM Agent FastAPI 使用指南（Human 版）

> 適用分支：`codex/llmwiki-feature`  
> API 版本：`0.6.0`
> 更新日期：2026-07-27

本文件提供給前端工程師、後端工程師、系統管理員與測試人員閱讀。若要讓 LLM / Agent 解析 API 契約，請改讀 [FastAPI LLM Reference](FastAPI_LLM_Reference.md)。執行中的欄位與 schema 最終仍以 `GET /openapi.json` 為準。

## 1. 服務入口

啟動後的預設入口：

| 用途 | 路徑 |
| --- | --- |
| API 根前綴 | `/api/v1` |
| Swagger UI（完全離線） | `/docs` |
| ReDoc（完全離線） | `/redoc` |
| OpenAPI JSON | `/openapi.json` |
| 存活檢查 | `GET /api/v1/health` |
| 相依服務檢查 | `GET /api/v1/health/dependencies` |

本機啟動：

```bash
uvicorn app.main:app --reload
```

## 2. 驗證與使用者身分

除 Health 與 `GET /api/v1/llmwiki/demo` 外，主要 API 都需要：

```http
Authorization: Bearer <token>
X-Request-ID: <可選追蹤識別碼>
```

目前分支使用本機開發 token 格式：

```text
使用者ID|部門|機密等級|角色1,角色2
```

範例：

```text
user001|RD|confidential|engineer,reviewer
```

管理員測試 token 固定為：

```text
admin
```

可用機密等級依序為 `public`、`internal`、`confidential`、`restricted`。使用者等級必須大於或等於文件等級。這套字串 token 是目前的本機開發驗證，不應直接視為正式環境的 SSO/JWT 實作。

## 3. 權限與跨部門規則

系統會以 token 解析出：

- `external_user_id`：使用者身分。
- `department`：部門。
- `clearance_level`：可讀機密等級。
- `roles`：角色集合。

知識庫與文件權限支援三種對象：`user`、`department`、`role`；權限值為 `read`、`write`、`admin`，目前讀取判斷接受 `read` 或 `admin`。

實際讀取規則：

1. `admin` 可讀全部。
2. Session 文件只限該 Session 擁有者，管理員除外。
3. 非 Session 文件先檢查文件機密等級。
4. 文件的 `department` 與使用者部門相同時直接允許讀取。
5. 跨部門時，先檢查知識庫規則，再檢查文件規則。
6. 知識庫或文件完全沒有設定規則時，該層預設開放；一旦新增任何規則，就變成 allow-list。
7. 封存文件不提供一般使用者讀取。

因此「跨部門」不是固定禁止：

- 未設定 allow-list 且機密等級足夠：可以跨部門。
- 已設定知識庫或文件 allow-list：使用者、部門或角色至少一項必須命中。
- 同部門文件目前會優先通過，不受後續 allow-list 限制。
- Session 文件不依部門共享，只看 Session 擁有者。

所有文件、知識庫與 Skill 權限的新增、查詢與刪除 API 均要求 `admin` 角色。

## 4. 常用流程

### 4.1 建立知識庫並上傳文件

建立知識庫：

```http
POST /api/v1/knowledge-bases
Authorization: Bearer admin
Content-Type: application/json
```

```json
{
  "name": "AOI Knowledge Base",
  "description": "AOI 檢測文件",
  "owner_department": "RD",
  "default_confidential_level": "internal"
}
```

查詢可上傳格式：

```http
GET /api/v1/documents/formats
Authorization: Bearer admin
```

上傳知識庫文件：

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/documents/upload" \
  -H "Authorization: Bearer admin" \
  -H "X-Request-ID: upload-001" \
  -F "file=@manual.pdf" \
  -F "scope=knowledge_base" \
  -F "knowledge_base_id=<KB_UUID>" \
  -F "confidential_level=internal" \
  -F "department=RD"
```

上傳成功只代表已排入背景處理。使用下列端點輪詢：

```http
GET /api/v1/documents/{document_id}/status
```

狀態流程通常為 `uploaded → parsing → ocr_processing（需要時）→ chunking → embedding → indexing → ready`。失敗為 `failed`，封存為 `archived`。

### 4.2 上傳只屬於對話的暫存文件

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/documents/upload" \
  -H "Authorization: Bearer user001|RD|internal|engineer" \
  -F "file=@temporary.pdf" \
  -F "scope=session" \
  -F "confidential_level=internal"
```

`scope=session` 時不得傳 `knowledge_base_id`。可傳既有 `session_id`；若省略，伺服器會建立並回傳新的 Session。刪除 Session 會連同該 Session 的訊息、暫存文件、切片與本機 artifact 一起刪除，且不可復原：

```http
DELETE /api/v1/chat/sessions/{session_id}
```

### 4.3 一般問答

```http
POST /api/v1/chat/query
Authorization: Bearer user001|RD|internal|engineer
Content-Type: application/json
```

```json
{
  "session_id": null,
  "knowledge_base_ids": ["<KB_UUID>"],
  "query": "請整理文件中的 AOI 檢測流程",
  "top_k": 8,
  "use_rerank": true,
  "use_masking": true,
  "use_tools": false
}
```

模式判斷：

| 條件 | 行為 |
| --- | --- |
| 有 `knowledge_base_ids` | 檢索指定知識庫，並合併同 Session 文件 |
| 無知識庫但 Session 有文件 | 只做 Session RAG |
| 無知識庫且 Session 沒有文件 | 直接使用 LLM 通用知識，不產生文件引用 |
| 有文件來源但找不到相關內容 | 維持嚴格 RAG，不用通用知識補答 |

串流版本為 `POST /api/v1/chat/stream`，回應 Content-Type 為 `text/event-stream`。

回答畫面會把主要答案、重點、來源、信心與限制分區顯示，並在下方顯示外部 LLM
回傳的輸入、輸出與總 token 數。API 的 `answer` 不再包含
`### 2. Key points` 至 `### 5. Limitations`；這些內容位於 `sections`。若模型
服務未提供 token 統計，前端不顯示用量。

### 4.4 程式碼問答

程式碼助理使用 `POST /api/v1/code-chat/query` 或 SSE 版本
`POST /api/v1/code-chat/stream`。請求可傳 `code`、`language`、`file_name` 與可選
行號，並可選擇使用者有讀取權限的技術知識庫。回覆會分出診斷、建議修改、風險、
程式碼區塊及 token usage。

可先把 `.py`、`.js`、`.ts`、`.json`、`.md` 等程式或設定檔上傳為 Session 暫存文件：

```http
POST /api/v1/documents/upload
scope=session
chat_type=code
confidential_level=internal
```

Code Session 不可拿去呼叫一般 `/chat/*`，反之亦然；刪除該 Session 時會一併刪除
暫存檔與聊天紀錄。貼上或上傳的程式碼只會送給 LLM 分析，不會由 LM Agent 執行。

### 4.5 LLMWiki

所有正式 LLMWiki 查詢都必須以重複 query parameter 傳入至少一個 `knowledge_base_ids`：

```http
GET /api/v1/llmwiki/search?knowledge_base_ids=<KB_UUID>&q=AOI&limit=10
POST /api/v1/llmwiki/topics/AOI/compile?knowledge_base_ids=<KB_UUID>&top_k=24
GET /api/v1/llmwiki/topics/AOI?knowledge_base_ids=<KB_UUID>
GET /api/v1/llmwiki/topics/AOI/graph?knowledge_base_ids=<KB_UUID>
GET /api/v1/llmwiki/index?knowledge_base_ids=<KB_UUID>
GET /api/v1/llmwiki/lint?knowledge_base_ids=<KB_UUID>
GET /api/v1/llmwiki/operations?limit=20
```

`GET /api/v1/llmwiki/demo` 不需登入，只回傳內建展示資料。

### 4.6 管理員檢查

```http
GET  /api/v1/admin/status
POST /api/v1/admin/llm/test
GET  /api/v1/admin/embedding/status
POST /api/v1/admin/embedding/test
GET  /api/v1/admin/operations/metrics
POST /api/v1/admin/retention
```

LLM 測試不啟動 RAG、LLMWiki 或資料庫 Session，適合單獨確認 OpenAI-compatible LLM 連線。

## 5. API 群組

| 群組 | 主要用途 | 一般權限 |
| --- | --- | --- |
| Health | 系統與相依服務健康狀態 | 公開 |
| Chat | 問答、SSE、Session 訊息與刪除 | 已驗證使用者 |
| Documents | 格式、上傳、列表、狀態、明細 | 已驗證；reindex/archive 僅 admin |
| Knowledge Bases | 建立與列出可存取知識庫 | 已驗證使用者 |
| Skills | Skill 列表、明細與檔案預覽 | 已驗證；異動僅 admin |
| LLMWiki | 搜尋、編譯、索引、圖、lint、operations | demo 公開，其餘已驗證 |
| Permissions | 文件、知識庫、Skill allow-list | admin |
| Audit | Chat、遮罩、檢索、LLM、拒絕事件 | admin |
| Admin | LLM、Embedding、監控、保留與敏感規則 | admin |

完整逐端點清單與資料型別請見 [FastAPI LLM Reference](FastAPI_LLM_Reference.md)。

## 6. 錯誤格式

```json
{
  "request_id": "upload-001",
  "error_code": "PERMISSION_DENIED",
  "message": "User does not have permission to access this document.",
  "details": {}
}
```

主要錯誤碼：

`INVALID_REQUEST`、`UNAUTHORIZED`、`PERMISSION_DENIED`、`DOCUMENT_NOT_FOUND`、`DOCUMENT_NOT_READY`、`SKILL_NOT_FOUND`、`DLP_BLOCKED`、`EMBEDDING_SERVICE_ERROR`、`LLM_SERVICE_ERROR`、`INTERNAL_ERROR`。

FastAPI 自身的 request validation error 仍可能依框架預設格式回傳；應以實際 `/openapi.json` 與 API 回應為準。

## 7. 維護準則

修改路由或 Pydantic schema 時，請同步更新：

1. `/openapi.json`（由程式自動產生）。
2. 本 Human 指南的流程與權限說明。
3. `FastAPI_LLM_Reference.md` 的端點清單與契約。
4. 舊版詳細規格 `Fastapi_spec.md` 中仍保留的範例。

程式碼的主要來源：

- `app/main.py`
- `app/api/v1/router.py`
- `app/api/v1/*.py`
- `app/schemas/*.py`
- `app/core/security.py`
- `app/services/permission_service.py`
