# 機敏資料規則管理教學

本文件給系統管理者維護 DLP / masking 規則使用。規則會寫入
`sensitive_dictionaries`，並在 query、RAG context、LLM response 三個位置套用。

## 規則欄位

- `entity_type`: 機敏資料類型，例如 `customer_name`、`machine_name`、`restricted_keyword`。
- `value`: 要偵測的精確文字，大小寫不敏感。
- `replacement`: 遮罩後顯示值，例如 `[CUSTOMER_001]`。若設定為 `[BLOCK]`，命中後會阻擋請求。
- `risk_level`: `low`、`medium`、`high`。
- `is_active`: 是否啟用。

## 建立或更新規則

```http
POST /api/v1/admin/sensitive-rules
Authorization: Bearer admin
Content-Type: application/json
```

```json
{
  "entity_type": "customer_name",
  "value": "Example Customer Corp",
  "replacement": "[CUSTOMER_001]",
  "risk_level": "medium",
  "is_active": true
}
```

同一組 `entity_type` + `value` 重複送出時會更新既有規則。

## 建立阻擋規則

將 `replacement` 設為 `[BLOCK]` 可阻擋命中的 query / context / response。

```json
{
  "entity_type": "restricted_keyword",
  "value": "RESTRICTED_FORMULA",
  "replacement": "[BLOCK]",
  "risk_level": "high",
  "is_active": true
}
```

## 查詢規則

```http
GET /api/v1/admin/sensitive-rules
Authorization: Bearer admin
```

## 啟用或停用規則

```http
PATCH /api/v1/admin/sensitive-rules/{rule_id}
Authorization: Bearer admin
Content-Type: application/json
```

```json
{
  "is_active": false
}
```

## 安裝範本規則

```http
POST /api/v1/admin/sensitive-rules/templates
Authorization: Bearer admin
```

範本預設為停用，管理者應先確認內容，再逐條啟用。

## 內建偵測

系統除了字典規則，也會自動偵測：

- email、電話、IP、URL、檔案路徑
- API key、password、token、secret
- lot / batch number
- machine / tool / EQP 名稱
- product / model / customer code
- temperature、pressure、voltage、current、rpm 等製程參數
- 簡易 person / organization pattern
- prompt injection，例如要求忽略前序指令、揭露 system prompt、停用安全規則

## Restricted context 規則

RAG context 若包含 `confidential_level=restricted` 或 `send_to_llm=false`，會在送入
LLM 前阻擋並寫入 `dlp_blocked` audit event。
