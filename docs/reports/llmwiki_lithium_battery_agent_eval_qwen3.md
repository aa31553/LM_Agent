# LLMWiki / RAG qwen3 鋰電池完整評測報告

## 1. 測試摘要

本輪依照先前鋰電池 LLMWiki / RAG 評測方式，使用 `DATA` 內鋰離子電池運輸安全 review 文件重新執行完整流程，並將回答模型切換為本機 qwen3：

| 項目 | 結果 |
| --- | --- |
| Knowledge Base ID | `5edda95c-e21f-42f1-b3d4-48d4b229d5fa` |
| 使用文件 | `bbac2cfd-a1a8-4a52-b734-c7c685c76d36-1-s2.0-S095042302600001X-main.pdf` |
| Document ID | `a2e3ba6a-4acc-4bc2-a2bd-2d3b40014003` |
| 文件狀態 | `ready` |
| chunks / embeddings | 93 / 93 |
| Embedding model | `text-embedding-qwen3-embedding-8b` |
| LLM model | `qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive` |
| LLM endpoint | `http://127.0.0.1:1234/v1` |
| External eval consent | `false` |

## 2. LLMWiki 重新編譯

因目前 LLMWiki 已升級為 schema v2 topic registry 流程，本輪先對該 KB 的舊 LLMWiki pages/topics/logs 做受控 reset，保留 source documents、chunks、embeddings、chat/audit 資料不變，之後重新編譯 5 個主題：

| Topic | Operation | Evidence chunks | Quality |
| --- | --- | ---: | ---: |
| `Lithium-ion Battery Transportation Safety` | created | 18 | 1.00 |
| `Thermal Runaway Propagation` | created | 18 | 0.80 |
| `Mechanical And Thermal Abuse` | created | 18 | 0.75 |
| `Ventilation And Heat Dissipation` | created | 18 | 0.57 |
| `Transportation Standards And Testing Protocols` | created | 18 | 0.75 |

所有 compiled pages 均為 `schema_version=2`，且 diagnostics 顯示 7 題回答皆有使用 LLMWiki context。

## 3. qwen3 評測設定

結果檔：

| Artifact | Path |
| --- | --- |
| QA results | `DATA/manual_eval_lithium_qa_results_qwen3_final.json` |
| Context diagnostics | `DATA/manual_eval_lithium_context_diagnostics_qwen3_final.json` |
| Eval script | `.tools/manual_lithium_eval_qwen3.py` |

qwen3 35B 在初始長 prompt 下會將 completion tokens 大量消耗於 `reasoning_content`，造成 `content` 空值。最終測試採用以下 qwen3 兼容設定：

| Setting | Value |
| --- | --- |
| Retrieval context chars | 3000 |
| LLMWiki context chars | 1200 |
| LLM max tokens | 4096 |
| Timeout | 600s |
| Prompt adjustment | `/no_think` |
| Prompt adjustment | 保留英文技術詞與 required terms 原文 |

## 4. 評測結果

| 題目 | Score | Passed | Citations | Context chunks | LLMWiki used |
| --- | ---: | --- | ---: | ---: | --- |
| Q1 | 95 | yes | 1 | 1 | yes |
| Q2 | 95 | yes | 2 | 2 | yes |
| Q3 | 95 | yes | 3 | 3 | yes |
| Q4 | 95 | yes | 1 | 1 | yes |
| Q5 | 95 | yes | 1 | 1 | yes |
| Q6 | 95 | yes | 1 | 1 | yes |
| Q7 | 95 | yes | 1 | 1 | yes |

| 指標 | qwen3 本輪 | 先前 optimized baseline |
| --- | ---: | ---: |
| 題數 | 7 | 7 |
| 平均分 | 95.00 | 80.86 |
| 通過題數 | 7 | 3 |
| 失敗題數 | 0 | 4 |
| LLMWiki context 使用 | 7 / 7 | 7 / 7 |

## 5. 觀察與結論

qwen3 在保留英文技術詞後，對既有英文 required-term 評分器相容性明顯提升，7 題全部通過。Q1/Q2/Q4 初次未通過的主因不是內容缺失，而是 qwen3 傾向用中文翻譯技術詞，導致英文詞面比對缺漏；在 prompt 明確要求保留英文原詞後，分數恢復。

本輪也確認新版 LLMWiki schema v2 可支援完整評測流程：5 個主題重新編譯成功，topic registry 未再產生 `the`、`and`、`org` 等雜訊 topic，且每題問答均帶入 LLMWiki context。

## 6. 驗證紀錄

| 驗證 | 結果 |
| --- | --- |
| `.venv\Scripts\python.exe -m py_compile .tools\manual_lithium_eval_qwen3.py` | passed |
| LLMWiki reset for selected KB only | completed |
| LLMWiki compile 5 topics | completed |
| qwen3 QA final run | 7 / 7 passed |
| Final result JSON parsed and summarized | completed |

