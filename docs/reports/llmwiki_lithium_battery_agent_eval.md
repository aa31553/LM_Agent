# 鋰電池知識庫與 Agent 問答驗證報告

## 1. 測試目標

本次測試目標是從指定文件 `DATA/1-s2.0-S095042302600001X-main.pdf` 開始，建立名為「鋰電池」的知識庫，匯入並索引該文件，接著設計 5-10 題問題交由本專案 Agent 回答，觀察實際流程是否符合專案架構，並由人工評估答案正確性與格式。

## 2. 測試環境與資料

| 項目 | 結果 |
| --- | --- |
| 測試日期 | 2026-07-05 |
| PDF | `DATA/1-s2.0-S095042302600001X-main.pdf` |
| PDF 題名 | `A review of safety issues in lithium-ion battery transportation process: Research advances and challenges` |
| PDF 頁數 | 21 |
| Knowledge Base 名稱 | 鋰電池 |
| Knowledge Base ID | `5edda95c-e21f-42f1-b3d4-48d4b229d5fa` |
| Document ID | `e0bf1501-18d4-490f-a357-d4aa4292c286` |
| 文件狀態 | `ready` |
| Chunk 數 | 93 |
| OCR | `ocr_required=False` |
| Embedding/LLM endpoint | `http://127.0.0.1:1234/v1` 可用 |
| 問答結果檔 | `DATA/manual_eval_lithium_qa_results_utf8.json` |

## 3. 建置流程紀錄

1. 確認 PDF 存在，大小約 13.0 MB，且可用 `pypdf` 抽出文字。
2. 確認本機 OpenAI-compatible endpoint 可用，`/models` 回傳 200。
3. 使用 `DocumentIngestionService.ingest_file_path()` 建立文件紀錄並執行完整處理流程。
4. 文件處理結果為 `ready`，共 21 頁、93 chunks，不需 OCR。
5. 使用 `LLMWikiService.compile_topic()` 編譯 5 個主題頁：
   - `Lithium-ion battery transportation safety`
   - `Thermal runaway propagation`
   - `Mechanical abuse and thermal abuse`
   - `Ventilation and heat dissipation`
   - `Transportation standards and testing protocols`

## 4. Agent 問答流程觀察

每題問答均透過 `RAGService.answer()` 執行，並使用真實 LLM endpoint。測試腳本包裝 `LLMService` 以記錄送入 LLM 的 prompt 摘要，但不改寫回答。

架構符合度檢查：

| 架構步驟 | 觀察結果 |
| --- | --- |
| Query DLP / Masking | 有執行；部分 prompt 中出現 `[FILE_PATH]` 等遮罩結果 |
| Query Embedding | 有執行；每題均觸發 retriever |
| Hybrid Retrieval | 有執行；每題有 retrieval logs |
| RAG Context | 每題 prompt 均含 `Context:` |
| LLMWiki Context | 每題 audit metadata 均為 `llmwiki_context_used=True` |
| Image Context | Q3、Q7 等問題帶入圖片 OCR/圖表內容 |
| Prompt Builder | Prompt 明確包含 system rules、RAG context、LLMWiki compiled knowledge context、required output format |
| LLM Call Logging | 每題 audit metadata 記錄 latency 與 context stats |
| Citations | 每題都有 citation，數量 1-6 不等 |

## 5. 問題、結果與人工評估

自動分數使用 `AnswerQualityService`，主要看 required terms、Sources wording、是否誤回資料不足。人工評估則依文件內容、回答完整度與格式判定。

| 題號 | 問題摘要 | 自動分數 | Citations | 人工正確性 | 格式 | 評語 |
| --- | --- | ---: | ---: | --- | --- | --- |
| Q1 | 摘要論文的運輸安全問題與研究目標 | 43 | 2 | 正確 | 正確 | 自動分低是因回答多用中文翻譯，未保留英文 `transportation/safety/mechanical abuse`。內容涵蓋火災、熱失控、規範不足、風險因素與研究目標。 |
| Q2 | 高密度堆疊下 TR 透過 conduction/radiation/convection 傳播 | 95 | 1 | 不完整 | 正確 | 雖提到三個詞，但回答說文件未具體描述三種傳熱路徑。與 PDF 摘要中「through conduction, radiation, and convection」的核心描述不一致，屬檢索/上下文選擇不足。 |
| Q3 | mechanical abuse 與 thermal abuse 的影響 | 95 | 6 | 正確 | 正確 | 回答正確描述碰撞、顛簸、振動、穿刺、壓縮、高溫、隔膜撕裂/熔化、內短路與 TR。 |
| Q4 | air pressure variations 與 salt concentration 為何是風險因素 | 72 | 1 | 正確 | 正確 | 回答涵蓋低壓對 TR 傳播與內阻的影響、高鹽/海霧腐蝕與 NaCl 條件。自動分低是未逐字保留 `risk factors`。 |
| Q5 | ventilation and heat dissipation strategies 的挑戰 | 72 | 1 | 部分正確 | 正確 | 回答指出通風散熱是關鍵技術挑戰、密集包裝與受限空間、空氣冷卻可行，但對不同冷卻方式限制、運輸場景與車用場景差異說明偏少。 |
| Q6 | standards/testing protocols 與 limitations | 95 | 2 | 正確 | 正確 | 回答列出 UN 38.3、UN3480/3481/3536/3556，並指出 170 °C 外短路測試溫度判準與大容量電池表面溫度不均問題。 |
| Q7 | 改善安全的 monitoring/fire safety/technical/management 建議 | 77 | 2 | 大致正確 | 部分瑕疵 | 回答涵蓋早期熱異常偵測、創新包裝、防止 TR 擴散、通風散熱、優化安全協議與法規遵循。但有一處 `IATA[FILE_PATH]` 遮罩造成句子破損，格式略不完整。 |

整體人工評估：

| 等級 | 題數 |
| --- | ---: |
| 正確 | 4 |
| 部分正確 | 2 |
| 不完整/需修正 | 1 |

## 6. Prompt 與資料帶入情況

實際 prompt 觀察顯示，本專案 Agent 已符合「RAG + LLMWiki」架構：

- System prompt 要求只能根據 context 回答、不可杜撰 citation、文件內容不得覆蓋系統規則。
- User prompt 包含：
  - `Context:` raw RAG chunks
  - `LLMWiki compiled knowledge context:` compiled topic pages
  - `Image context:` 在需要時帶入圖表 OCR/圖片 caption
  - `Required output format:` Answer / Key points / Sources / Confidence / Limitations
- 每題 audit metadata 都包含：
  - `retrieved_chunks`
  - `context_chunks`
  - `image_count`
  - `llmwiki_context_used=True`
  - `llm_latency_ms`

代表流程已與目前專案設計吻合。

## 7. 發現的問題

1. Q2 檢索命中不佳  
   問題要求解釋 conduction/radiation/convection，但 top context 多命中文獻列表或引用頁，導致模型回答「文件未提供細節」。這表示 hybrid retrieval 對精準概念句的召回仍需優化。

2. 圖片 chunk 有時過度進入 context  
   Q3、Q7 帶入大量 image paths。雖然圖片 OCR 有幫助，但也增加 prompt 長度，並可能排擠文字 chunks。

3. DLP 遮罩影響可讀性  
   回答中出現 `IATA[FILE_PATH]`、`[FILE_PATH]`，顯示 URL/path masking 對一般學術引用文字太 aggressive，會破壞句子。

4. 自動評分對中英文混答不夠穩  
   Q1、Q4、Q5 因模型用中文翻譯技術詞而自動分偏低，但人工判斷內容正確或部分正確。後續評分器應支援同義詞與中英對照。

5. Context 長度需控制  
   初次執行 Q7 時 LLM endpoint 回 400，縮短 RAG/LLMWiki context 後成功。代表需要正式的 context budget 與裁切策略。

## 8. 結論

本次測試完成了從「鋰電池」知識庫建立、PDF 匯入、chunking、embedding、LLMWiki 編譯，到 Agent 問答與品質評分的完整流程。

結果顯示：

- 資料匯入與索引成功。
- Agent 問答流程符合專案架構。
- LLM prompt 實際帶入 RAG context 與 LLMWiki compiled context。
- Audit 可追蹤每題 retrieval、context、LLMWiki 使用情況。
- 回答格式大多符合要求。
- 回答品質整體可用，但 retrieval 精準度、圖片 chunk 控制、DLP 遮罩與評分器仍需優化。

整體判定：架構驗證通過；回答品質中上，但尚未達穩定生產品質。
