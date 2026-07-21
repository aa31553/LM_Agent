# Embedding Service Administration

## Architecture

The standalone service lives in `services/embedding_service/` and owns model loading and
inference only. LM Agent remains the authenticated control plane and calls the service over an
OpenAI-compatible HTTP boundary.

```text
LM Agent document/chat pipeline
  -> app/integrations/embedding_client.py
  -> POST http://127.0.0.1:1234/v1/embeddings
  -> local SentenceTransformers model

LM Agent Admin UI
  -> /api/v1/admin/embedding/status or /test
  -> standalone /status or /v1/embeddings
```

The standalone process has no database credentials and does not read documents, knowledge
bases, Sessions, DLP rules, or LLM settings.

## Windows startup

1. Copy a complete SentenceTransformers-compatible model directory to the offline host.
2. Create `services\embedding_service\.venv` and install its `requirements.txt`.
3. Set at least `EMBEDDING_MODEL_PATH` and choose `EMBEDDING_DEVICE=cpu`, `cuda`, or `auto`.
4. Run:

```bat
services\embedding_service\start_embedding_service.bat 1234
```

The first parameter is the API port. The console prints the API and Swagger URLs and remains
open after failure so the operator can read the error. Runtime model downloads are disabled.

## Required alignment

The following values must agree between the service, LM Agent, and PostgreSQL:

| Setting | Requirement |
| --- | --- |
| `EMBEDDING_MODEL_NAME` / `EMBEDDING_MODEL` | Same model identity |
| Service model dimension / `EMBEDDING_DIMENSION` | Exact match |
| `VECTOR(n)` | `n` equals the configured dimension |
| `EMBEDDING_SERVICE_API_KEY` / `EMBEDDING_API_KEY` | Same secret, or both empty on localhost |

Changing the model or dimension invalidates existing vectors. Re-embed all document chunks and
rebuild the pgvector column/index before serving retrieval traffic.

## Health and monitoring

- `/health/live` checks the HTTP process.
- `/health/ready` returns 503 until the model is loaded.
- `/status` returns JSON for the LM Agent Admin page.
- `/metrics` returns Prometheus text counters.
- `/docs` documents the service contract.

If the model path or dependency is invalid, the process intentionally remains alive with
`model_state=error`; read `/status` or the Admin UI for the actionable error.

## LM Agent Admin API

```http
GET  /api/v1/admin/embedding/status
POST /api/v1/admin/embedding/test
Authorization: Bearer admin
```

Test payload:

```json
{"text": "AOI defect inspection / 自動光學檢測"}
```

The response reports actual/configured dimensions, end-to-end latency, vector norm, and only the
first eight values. API keys are never returned.

## Where to extend

- Service API and validation: `services/embedding_service/app.py`
- Model backend/device behavior: `services/embedding_service/runtime.py`
- Service environment parsing: `services/embedding_service/config.py`
- LM Agent HTTP adapter: `app/integrations/embedding_client.py`
- Admin proxy endpoints: `app/api/v1/admin.py`
- Admin UI: `frontend/app.js`
