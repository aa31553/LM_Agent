# 公司內部 AI Agent / RAG 問答系統設計文件 v0.1

## 專案前提

本專案為公司內部 AI Agent 後端系統，前端由另一專案負責，後端使用 FastAPI 提供 API 服務。系統初始功能聚焦於廠內文獻與資料的向量資料庫建立，並提供使用者針對文獻與資料進行問答。

目前條件如下：

| 項目        | 規格                          |
| --------- | --------------------------- |
| 部署環境      | 公司內網伺服器，單機部署                |
| 前端        | 由另一專案負責                     |
| 後端        | FastAPI                     |
| LLM       | 公司內部 OpenAI-compatible API  |
| Embedding | 必須本地化，由另一模組或服務負責            |
| 初期資料類型    | PDF、圖片                      |
| 資料語言      | 英文約 75%、中文約 20%、其他外文約 5%    |
| 初期資料量     | 約 700–1000 份文獻              |
| 使用者登入     | 前端先處理，後端需再次確認權限             |
| 資料分級      | 公司已有基礎資料分級制度                |
| 初期輸出      | 問答為主，不產生 Word / Excel / PPT |
| 未來擴充      | 預留檔案伺服器接口                   |

---

# 文件一：系統架構設計書

## 1. 目的

本文件定義公司內部 AI Agent / RAG 問答系統的整體架構、主要模組、資料流、部署方式與系統邊界。

系統核心目標為：

1. 支援 PDF / 圖片文獻匯入。
2. 將文獻內容轉換為可檢索的向量資料。
3. 提供使用者對授權資料進行問答。
4. 在呼叫 LLM 前後執行機密資訊遮罩與去識別。
5. 後端再次確認使用者權限與資料分級。
6. 回答需附上文件來源，降低幻覺與不可追溯問題。
7. 保留未來串接檔案伺服器與內部系統的擴充介面。

---

## 2. 系統範圍

### 2.1 MVP 納入範圍

| 功能              | 是否納入 |
| --------------- | ---- |
| PDF 上傳          | 是    |
| 圖片上傳            | 是    |
| 掃描型 PDF OCR     | 是    |
| 圖片 OCR          | 是    |
| 文件文字抽取          | 是    |
| Chunking        | 是    |
| 本地 Embedding 串接 | 是    |
| 向量資料庫建立         | 是    |
| 問答 API          | 是    |
| 回答引用來源          | 是    |
| 後端權限檢查          | 是    |
| 資料分級判斷          | 是    |
| 機密資訊遮罩          | 是    |
| Audit Log       | 是    |
| 檔案伺服器接口預留       | 是    |

### 2.2 MVP 暫不納入範圍

| 功能                    | 說明                  |
| --------------------- | ------------------- |
| Word / Excel / PPT 產生 | 初期只回答，不產生文件         |
| 複雜 Agent Tool Calling | 等 RAG 穩定後再導入        |
| MES / ERP 串接          | 未來視需求擴充             |
| 多機部署 / Kubernetes     | 初期單機 Docker Compose |
| 圖表深度理解                | 初期以 OCR 與文字問答為主     |
| 表格複雜推理                | 可保留表格文字，但不作為第一階段重點  |

---

## 3. 整體架構

```text
[Frontend System]
    |
    | HTTP / SSE
    v
[FastAPI Backend]
    |
    +-- Auth & Permission Service
    |
    +-- Document Ingestion Service
    |       |
    |       +-- PDF Parser
    |       +-- Image OCR
    |       +-- Text Cleaner
    |       +-- Chunking Service
    |
    +-- Embedding Service Adapter
    |       |
    |       +-- Local Embedding API / Local Embedding Module
    |
    +-- Vector Store Service
    |       |
    |       +-- PostgreSQL + pgvector
    |       +-- Keyword Search Index
    |
    +-- RAG Service
    |       |
    |       +-- Query Preprocess
    |       +-- Hybrid Search
    |       +-- Rerank
    |       +-- Context Builder
    |       +-- Citation Builder
    |
    +-- DLP / Masking Service
    |       |
    |       +-- Query Masking
    |       +-- Context Masking
    |       +-- Response Scan
    |
    +-- LLM Service
    |       |
    |       +-- Company OpenAI-compatible API
    |
    +-- Audit Service
            |
            +-- Query Log
            +-- Retrieval Log
            +-- Masking Log
            +-- LLM Call Log
```

---

## 4. 主要模組說明

### 4.1 Frontend System

前端負責：

1. 使用者登入。
2. 傳送使用者問題。
3. 顯示 AI 回答。
4. 顯示引用來源。
5. 顯示文件處理狀態。
6. 提供文件上傳介面。

後端不可完全信任前端傳入的使用者資訊，仍需驗證 token、角色、分級與文件權限。

---

### 4.2 FastAPI Backend

後端為本系統核心控制層，負責：

1. API 路由。
2. 文件處理任務派發。
3. 權限檢查。
4. RAG pipeline 執行。
5. DLP / masking。
6. LLM API 呼叫。
7. Audit log 紀錄。
8. 系統狀態查詢。

---

### 4.3 Document Ingestion Service

負責將 PDF / 圖片轉成可檢索文字資料。

處理流程：

```text
File Upload
  ↓
Create Document Record
  ↓
Detect File Type
  ↓
PDF Text Extraction or OCR
  ↓
Text Cleaning
  ↓
Language Detection
  ↓
Chunking
  ↓
Embedding
  ↓
Indexing
  ↓
Document Ready
```

---

### 4.4 Embedding Service Adapter

Embedding 必須本地化，因此後端不直接綁定特定 embedding model，而是透過 adapter 呼叫本地 embedding 服務。

建議標準介面：

```text
POST /internal/embedding/embed
```

主要目的：

1. 隔離 RAG 後端與 embedding 模型。
2. 方便未來更換模型。
3. 支援批次 embedding。
4. 保留 model version 與 vector dimension 紀錄。

---

### 4.5 Vector Store Service

初期建議使用：

```text
PostgreSQL + pgvector
```

原因：

1. 單機部署簡單。
2. 權限 metadata filter 容易整合。
3. 不需額外維護大型向量資料庫。
4. 適合 700–1000 份文獻的 MVP 規模。
5. 可同時儲存文件 metadata、chunk、audit log。

---

### 4.6 RAG Service

RAG Service 負責：

1. 接收使用者問題。
2. 進行 query normalization。
3. 執行向量檢索與關鍵字檢索。
4. 合併檢索結果。
5. 進行權限過濾。
6. 執行 rerank。
7. 組合 context。
8. 呼叫 LLM。
9. 建立 citation。
10. 回傳答案。

---

### 4.7 DLP / Masking Service

DLP / Masking Service 負責：

1. 使用者問題送出前遮罩。
2. RAG context 送 LLM 前遮罩。
3. LLM 回答回傳前檢查。
4. 對高風險資料進行拒絕或阻擋。
5. 紀錄遮罩事件。

---

### 4.8 Permission Service

負責後端二次權限檢查：

1. 驗證使用者身份。
2. 確認使用者部門。
3. 確認使用者角色。
4. 確認使用者 clearance level。
5. 確認使用者可查詢的 knowledge base。
6. 確認 retrieved chunks 是否可被使用。

---

### 4.9 LLM Service

LLM Service 負責呼叫公司內部 OpenAI-compatible API。

主要職責：

1. 組合 ChatCompletion 請求。
2. 設定 model、temperature、max_tokens。
3. 處理 timeout。
4. 處理 retry。
5. 支援 streaming 預留。
6. 紀錄 token usage 與 latency。

---

### 4.10 Audit Service

Audit Service 負責紀錄：

1. 使用者問題。
2. 遮罩後問題。
3. 檢索到的 chunk。
4. 實際送入 context 的 chunk。
5. LLM 呼叫狀態。
6. 回答內容。
7. 遮罩事件。
8. 權限拒絕事件。
9. 錯誤事件。

---

## 5. 單機部署架構

建議以 Docker Compose 部署。

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

### 5.1 Container 建議

| Container         | 功能                      |
| ----------------- | ----------------------- |
| nginx             | Reverse proxy           |
| api               | FastAPI API server      |
| worker            | 文件解析、OCR、embedding 背景任務 |
| postgres          | PostgreSQL + pgvector   |
| redis             | Queue / cache           |
| embedding-service | 本地 embedding 服務，由另一專案提供 |
| file-storage      | 本機檔案目錄掛載                |

---

## 6. 系統狀態

文件狀態建議如下：

| 狀態             | 說明    |
| -------------- | ----- |
| uploaded       | 已上傳   |
| parsing        | 解析中   |
| ocr_processing | OCR 中 |
| chunking       | 切分中   |
| embedding      | 向量化中  |
| indexing       | 索引建立中 |
| ready          | 可查詢   |
| failed         | 處理失敗  |
| archived       | 已封存   |

---

## 7. 系統風險與對策

| 風險             | 對策                                |
| -------------- | --------------------------------- |
| OCR 品質不穩       | 保存 OCR confidence，低信心頁面標記         |
| 使用者越權查詢        | pre-filter + post-filter          |
| LLM 幻覺         | 強制引用來源，無資料時回答不足                   |
| 機密外洩           | query/context/response 三層 DLP     |
| 文件版本混亂         | 文件版本與狀態管理                         |
| Embedding 模型更換 | adapter interface 隔離              |
| 未來資料源擴充困難      | storage 與 ingestion interface 抽象化 |

---