

# 文件三：資料庫 Schema 設計書

## 1. 設計原則

1. PostgreSQL 作為主要資料庫。
2. pgvector 作為向量儲存。
3. 文件 metadata、chunk、權限、問答紀錄、稽核紀錄均儲存在 PostgreSQL。
4. 原始 PDF / 圖片存放在 filesystem，DB 僅保存路徑與 metadata。
5. 所有可查詢資料需有 confidential_level。
6. 所有 chunk 需可追溯至 document。
7. 所有回答需可追溯至 retrieval logs。

---

## 2. Extension

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
```

---

## 3. users

```sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    external_user_id TEXT UNIQUE NOT NULL,
    username TEXT,
    display_name TEXT,
    department TEXT,
    clearance_level TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

用途：

1. 保存前端登入使用者在後端的 snapshot。
2. 支援後端權限檢查。
3. 支援 audit log 查詢。

---

## 4. roles

```sql
CREATE TABLE roles (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
```

---

## 5. user_roles

```sql
CREATE TABLE user_roles (
    user_id UUID NOT NULL REFERENCES users(id),
    role_id UUID NOT NULL REFERENCES roles(id),
    PRIMARY KEY (user_id, role_id)
);
```

---

## 6. knowledge_bases

```sql
CREATE TABLE knowledge_bases (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    description TEXT,
    owner_department TEXT,
    default_confidential_level TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

---

## 7. documents

```sql
CREATE TABLE documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    knowledge_base_id UUID NOT NULL REFERENCES knowledge_bases(id),
    filename TEXT NOT NULL,
    original_filename TEXT,
    title TEXT,
    file_type TEXT NOT NULL,
    file_path TEXT NOT NULL,
    source_type TEXT DEFAULT 'manual_upload',
    language TEXT,
    confidential_level TEXT NOT NULL,
    department TEXT,
    version TEXT,
    status TEXT NOT NULL,
    page_count INT,
    chunk_count INT DEFAULT 0,
    ocr_required BOOLEAN DEFAULT FALSE,
    ocr_confidence FLOAT,
    error_message TEXT,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

建議 index：

```sql
CREATE INDEX idx_documents_kb ON documents(knowledge_base_id);
CREATE INDEX idx_documents_status ON documents(status);
CREATE INDEX idx_documents_confidential_level ON documents(confidential_level);
CREATE INDEX idx_documents_department ON documents(department);
```

---

## 8. document_chunks

注意：`VECTOR(1024)` 的維度需依本地 embedding 模型決定，以下先以 1024 為例。

```sql
CREATE TABLE document_chunks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES documents(id),
    knowledge_base_id UUID NOT NULL REFERENCES knowledge_bases(id),
    chunk_index INT NOT NULL,
    content TEXT NOT NULL,
    masked_content TEXT,
    section_title TEXT,
    page_start INT,
    page_end INT,
    language TEXT,
    source_type TEXT,
    token_count INT,
    confidential_level TEXT NOT NULL,
    metadata JSONB,
    embedding VECTOR(1024),
    created_at TIMESTAMP DEFAULT NOW()
);
```

建議 index：

```sql
CREATE INDEX idx_chunks_document_id ON document_chunks(document_id);
CREATE INDEX idx_chunks_kb_id ON document_chunks(knowledge_base_id);
CREATE INDEX idx_chunks_confidential_level ON document_chunks(confidential_level);
CREATE INDEX idx_chunks_language ON document_chunks(language);
CREATE INDEX idx_chunks_metadata ON document_chunks USING GIN(metadata);
```

pgvector index：

```sql
CREATE INDEX idx_chunks_embedding
ON document_chunks
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);
```

若資料量不大，初期也可先不建 ivfflat index，待資料量增加後再建立。

---

## 9. document_permissions

```sql
CREATE TABLE document_permissions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES documents(id),
    subject_type TEXT NOT NULL,
    subject_value TEXT NOT NULL,
    permission TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
```

subject_type 範例：

| subject_type | subject_value 範例 |
| ------------ | ---------------- |
| user         | user-001         |
| role         | engineer         |
| department   | R&D              |

permission 範例：

| permission | 說明  |
| ---------- | --- |
| read       | 可讀取 |
| write      | 可修改 |
| admin      | 可管理 |

---

## 10. knowledge_base_permissions

```sql
CREATE TABLE knowledge_base_permissions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    knowledge_base_id UUID NOT NULL REFERENCES knowledge_bases(id),
    subject_type TEXT NOT NULL,
    subject_value TEXT NOT NULL,
    permission TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
```

---

## 11. chat_sessions

```sql
CREATE TABLE chat_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id),
    title TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

---

## 12. chat_messages

```sql
CREATE TABLE chat_messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id UUID NOT NULL REFERENCES chat_sessions(id),
    user_id UUID REFERENCES users(id),
    role TEXT NOT NULL,
    original_content TEXT,
    masked_content TEXT,
    final_content TEXT,
    risk_level TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
```

role 範例：

| role      | 說明    |
| --------- | ----- |
| user      | 使用者   |
| assistant | AI 回答 |
| system    | 系統    |

---

## 13. retrieval_logs

```sql
CREATE TABLE retrieval_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    message_id UUID NOT NULL REFERENCES chat_messages(id),
    query TEXT NOT NULL,
    document_id UUID REFERENCES documents(id),
    chunk_id UUID REFERENCES document_chunks(id),
    vector_score FLOAT,
    keyword_score FLOAT,
    rerank_score FLOAT,
    final_score FLOAT,
    rank INT,
    used_in_context BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);
```

---

## 14. masking_events

```sql
CREATE TABLE masking_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    message_id UUID REFERENCES chat_messages(id),
    entity_type TEXT NOT NULL,
    original_value TEXT,
    masked_value TEXT,
    masking_method TEXT,
    confidence FLOAT,
    risk_level TEXT,
    location TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
```

location 範例：

| location | 說明          |
| -------- | ----------- |
| query    | 使用者問題       |
| context  | RAG context |
| response | LLM 回答      |

---

## 15. llm_call_logs

```sql
CREATE TABLE llm_call_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    message_id UUID NOT NULL REFERENCES chat_messages(id),
    model_name TEXT,
    prompt_tokens INT,
    completion_tokens INT,
    total_tokens INT,
    latency_ms INT,
    status TEXT,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
```

---

## 16. document_processing_jobs

```sql
CREATE TABLE document_processing_jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES documents(id),
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    progress INT DEFAULT 0,
    error_message TEXT,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);
```

job_type 範例：

| job_type | 說明        |
| -------- | --------- |
| parse    | 文字解析      |
| ocr      | OCR       |
| chunk    | 切分        |
| embed    | embedding |
| index    | 索引        |
| reindex  | 重新索引      |

---

## 17. sensitive_dictionaries

```sql
CREATE TABLE sensitive_dictionaries (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_type TEXT NOT NULL,
    value TEXT NOT NULL,
    replacement TEXT,
    risk_level TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);
```

用途：

1. 客戶名稱。
2. 產品代號。
3. 專案代號。
4. 機台代號。
5. 內部敏感詞。

---

## 18. prompt_templates

```sql
CREATE TABLE prompt_templates (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    template TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);
```

---

## 19. document_images

The image extraction extension stores figures and page-render fallbacks separately from
text chunks. Each image remains traceable to its source document and knowledge base, and
may be linked to the image-derived chunk used for retrieval.

```sql
CREATE TABLE document_images (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES documents(id),
    knowledge_base_id UUID NOT NULL REFERENCES knowledge_bases(id),
    chunk_id UUID REFERENCES document_chunks(id),
    page_number INT NOT NULL,
    image_index INT NOT NULL,
    caption TEXT,
    image_path TEXT NOT NULL,
    mime_type TEXT,
    width INT,
    height INT,
    ocr_text TEXT,
    extraction_method TEXT NOT NULL,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);
```

Images are saved on the filesystem. The database stores paths, captions, OCR text, and
metadata only. RAG access control is inherited from the source document/chunk permission
filter.

---
