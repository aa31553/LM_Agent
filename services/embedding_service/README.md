# LM Agent Embedding Service

This directory contains an independent, offline-first, OpenAI-compatible embedding API.
It does not import the LM Agent database or RAG services and can run on another Windows host.

## Endpoints

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/embeddings` | OpenAI-compatible embeddings |
| `GET /v1/models` | Available model |
| `GET /health/live` | Process liveness |
| `GET /health/ready` | Model readiness; returns 503 until ready |
| `GET /status` | JSON model and runtime metrics |
| `GET /metrics` | Prometheus text metrics |
| `GET /docs` | Swagger UI |

## Windows setup

Create an isolated environment and install dependencies:

```bat
cd services\embedding_service
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Download the embedding model on an online machine, copy the complete model directory to the
offline server, and set its local path before starting:

```bat
set EMBEDDING_MODEL_PATH=D:\AI_Models\text-embedding-mxbai-embed-large-v1
set EMBEDDING_MODEL_NAME=text-embedding-mxbai-embed-large-v1
set EMBEDDING_DEVICE=cuda
start_embedding_service.bat 1234
```

The first argument overrides the API port. If omitted, `EMBEDDING_PORT` is used, then `1234`.
The window remains open after an error so the reason is visible. The script forces Hugging Face
and Transformers offline mode; no model is downloaded at runtime.

If you set `EMBEDDING_SERVICE_API_KEY`, configure the same value as `EMBEDDING_API_KEY` in LM
Agent. Leave both empty only on a trusted localhost connection.

## LM Agent configuration

```env
EMBEDDING_ENDPOINT=http://127.0.0.1:1234/v1/embeddings
EMBEDDING_SERVICE_BASE_URL=http://127.0.0.1:1234
EMBEDDING_MODEL=text-embedding-mxbai-embed-large-v1
EMBEDDING_DIMENSION=1024
EMBEDDING_API_KEY=
EMBEDDING_TIMEOUT_SECONDS=90
EMBEDDING_SSL_VERIFY=true
```

`EMBEDDING_DIMENSION` must exactly match the model dimension and the pgvector schema. Changing
models or dimensions requires re-embedding existing chunks and updating the vector column/index.

## Direct test

```powershell
$body = @{
  model = "text-embedding-mxbai-embed-large-v1"
  input = @("AOI defect inspection", "自動光學檢測")
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri "http://127.0.0.1:1234/v1/embeddings" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```
