# LLMWiki / RAG 品質優化與鋰電池驗證報告

## 1. 本輪目標

本輪針對前一輪 LLMWiki 品質不佳、RAG 資料帶入不穩定、以及 embedding 模型品質疑慮進行優化。主要變更為：

- 將鋰電池知識庫改用本地 `text-embedding-qwen3-embedding-8b` 重新建立索引。
- 移除舊文件向量，避免舊 embedding 混入新配置。
- 改善 RAG retrieval、keyword search、圖片 chunk 控制與 LLMWiki evidence 排序。
- 使用另一個本機 LLM 模型重新跑 7 題驗證。

使用者指定的外部 endpoint 未實際送出私有知識庫內容。原因是 sandbox escalation 被拒絕，系統判定把私有 workspace 文件與 prompt 傳到未信任外部 LLM endpoint 屬於資料外洩風險。

評測腳本已加入外部端點安全門檻：若 `LLM_BASE_URL` 不是 localhost，必須額外設定 `ALLOW_EXTERNAL_LLM_EVAL=I_UNDERSTAND_PRIVATE_CONTEXT_WILL_BE_SENT`。這個旗標只應在使用者明確同意把檢索到的私有文件內容與 prompt 送到外部服務後使用。

## 2. 資料與向量狀態

| 項目 | 值 |
| --- | --- |
| Knowledge Base ID | `5edda95c-e21f-42f1-b3d4-48d4b229d5fa` |
| 新 qwen3 Document ID | `a2e3ba6a-4acc-4bc2-a2bd-2d3b40014003` |
| 新文件狀態 | `ready` |
| 新文件 chunks / embeddings | 93 / 93 |
| 舊 Document ID | `e0bf1501-18d4-490f-a357-d4aa4292c286` |
| 舊文件狀態 | `archived` |
| 舊文件 chunks / embeddings | 93 / 0 |
| LLMWiki 舊文件 evidence | 0 |
| LLMWiki 新文件 evidence | 90 |

舊 chunks 因為已被 `retrieval_logs` 外鍵引用，不能直接刪除。已改為清空舊 chunks 的 `embedding` 欄位，保留稽核紀錄但移除舊向量。

## 3. 主要優化

| 項目 | 狀態 | 說明 |
| --- | --- | --- |
| qwen3 embedding 重建 | 完成 | 新文件用 `text-embedding-qwen3-embedding-8b` 建立 93 個向量 |
| 舊向量移除 | 完成 | 舊文件 embedding count 已驗證為 0 |
| 4096 維相容 | 完成待優化 | qwen3 endpoint 回 4096 維，目前因 DB schema 是 `VECTOR(1024)`，先裁切到 1024 維 |
| 批次 embedding | 完成 | 新增 `embedding_batch_size`，避免本地 8B embedding timeout |
| Retrieval metadata | 完成 | vector/keyword search 回傳 `source_type`、頁碼、section、title |
| Keyword search | 完成 | 從整句 `ILIKE` 改成關鍵詞 OR 查詢 |
| RAG over-fetch | 完成 | 先抓 3 倍候選，再依圖片需求過濾並裁回 `top_k` |
| LLMWiki 降噪 | 完成 | 降權 references、低資訊 chunk、非必要圖片 chunk |
| LLMWiki 舊 evidence 清理 | 完成 | 刪除仍指向舊 archived 文件的舊 compiled page |

## 4. LLMWiki 重新編譯

已重新編譯 5 個主題，每個主題使用 18 個 evidence chunks、1 份新 qwen3 文件來源：

| Topic | 狀態 |
| --- | --- |
| `Lithium-ion battery transportation safety` | updated |
| `Thermal runaway propagation` | updated |
| `Mechanical abuse and thermal abuse` | updated |
| `Ventilation and heat dissipation` | updated |
| `Transportation standards and testing protocols` | updated |

## 5. Context 診斷

診斷檔：`DATA/manual_eval_lithium_context_diagnostics_optimized.json`

| 題目 | retrieved | filtered | used context chunks | used image chunks | LLMWiki context chars |
| --- | ---: | ---: | ---: | ---: | ---: |
| Q1 | 24 | 8 | 5 | 0 | 7014 |
| Q2 | 24 | 8 | 7 | 2 | 2762 |
| Q3 | 24 | 8 | 8 | 2 | 7014 |
| Q4 | 24 | 8 | 5 | 0 | 7014 |
| Q5 | 24 | 8 | 5 | 0 | 7014 |
| Q6 | 24 | 8 | 6 | 0 | 7014 |
| Q7 | 24 | 8 | 5 | 0 | 7014 |

摘要：

| 指標 | 結果 |
| --- | ---: |
| 題數 | 7 |
| 平均 used context chunks | 5.86 |
| 平均 LLMWiki context chars | 6406.57 |
| 圖片 chunk 使用範圍 | Q2、Q3 |

## 6. 回答評測

結果檔：`DATA/manual_eval_lithium_qa_results_optimized.json`

外部 endpoint 因資料外洩風險被 sandbox 拒絕後，改用本機不同模型做安全替代評測：

| 設定 | 值 |
| --- | --- |
| LLM endpoint | `http://127.0.0.1:1234/v1` |
| LLM model | `gemma4-26b-a4b-uncensored-hauhaucs-balanced` |
| Embedding model | `text-embedding-qwen3-embedding-8b` |
| Effective vector dimension | 1024 |
| top_k | 8 |
| External eval consent flag | `false` |

| 題目 | 分數 | 通過 | Citations | 備註 |
| --- | ---: | --- | ---: | --- |
| Q1 | 60 | 否 | 3 | 仍缺部分 required terms 或 Sources 格式完整度 |
| Q2 | 77 | 否 | 4 | 熱傳機制回答較完整，但未達 scoring gate |
| Q3 | 95 | 是 | 5 | mechanical/thermal abuse 與 internal short circuit 命中 |
| Q4 | 72 | 否 | 3 | risk factors 有命中但完整性不足 |
| Q5 | 72 | 否 | 3 | ventilation / heat dissipation 有帶入，但限制面不足 |
| Q6 | 95 | 是 | 3 | standards / protocols / limitations 命中 |
| Q7 | 95 | 是 | 3 | monitoring / fire safety / technical / management 命中 |

| 指標 | 前一輪 | 本輪 |
| --- | ---: | ---: |
| 題數 | 7 | 7 |
| 平均分數 | 78.43 | 80.86 |
| 通過題數 | 3 | 3 |
| 錯誤題數 | 0 | 0 |

本輪平均分數提升 2.43 分，主要改善集中在 Q1、Q7；Q2 較前一輪下降，顯示 thermal propagation 的回答仍需要更精準的 evidence selection 或 prompt 約束。

## 7. 測試結果

| 測試 | 結果 |
| --- | --- |
| `python -m py_compile ...` | 通過 |
| `node --check frontend/app.js` | 通過 |
| `pytest tests/test_llmwiki_api.py tests/test_llmwiki_rag_integration.py -q` | 3 passed |
| `pytest -q` | 45 passed, 1 skipped |

## 8. 結論

本輪已完成 RAG/LLMWiki 的本地品質優化、qwen3 embedding 重建、舊向量清空、LLMWiki 舊 evidence 清理，以及完整 7 題安全替代評測。資料帶入穩定性已改善：每題都有 RAG context 與 LLMWiki context，非必要圖片 chunk 不再大量占用 context。

尚未完成的是使用指定外部 endpoint 進行評測。該步驟被 sandbox 明確拒絕，原因是會把私有知識庫內容傳送到未信任外部服務。若後續要執行外部 endpoint 評測，需要使用者在了解資料會離開本機 workspace 的風險後，明確批准該資料外送行為。
