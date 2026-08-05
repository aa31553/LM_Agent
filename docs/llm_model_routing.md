# External LLM model routing

All request-driven external generative LLM paths use the same server-side route allowlist.
JSON request bodies accept optional `model` and `thinking_mode` fields on:

- `POST /api/v1/chat/query`
- `POST /api/v1/chat/stream`
- `POST /api/v1/code-chat/query`
- `POST /api/v1/code-chat/stream`
- `POST /api/v1/analysis/plan-drafts`
- `POST /api/v1/analysis/intent-drafts`
- `POST /api/v1/analysis/jobs/{job_id}/explain`
- `POST /api/v1/analysis/hybrid-answer`
- `POST /api/v1/admin/llm/test`

```json
{
  "query": "Summarize the attached file",
  "model": "reasoning",
  "thinking_mode": "high"
}
```

`model` is a server-configured route key, not an upstream URL. `thinking_mode` is one of
`default`, `none`, `low`, `medium`, or `high`. Omitting both fields preserves the existing
single-model behavior. `default` uses the selected route's configured `reasoning_effort`,
while `none` omits `reasoning_effort` from the upstream request.

The LLMWiki endpoints use query parameters because search is a `GET` request:

```http
GET /api/v1/llmwiki/search?knowledge_base_ids=<UUID>&q=topic&model=reasoning&thinking_mode=high
POST /api/v1/llmwiki/topics/topic/compile?knowledge_base_ids=<UUID>&model=reasoning&thinking_mode=high
```

The LLMWiki topic-review CLI accepts the equivalent `--model` and `--thinking-mode` options.
Admin test responses report the resolved route key, upstream model, thinking mode, and endpoint.
`GET /api/v1/health/dependencies` reports every configured route without exposing credentials.
Chat LLM audit logs store the actual resolved upstream model instead of the legacy global model.

Configure model choices with the `LLM_MODEL_ROUTES` JSON environment variable. Each key is
an allowed frontend selection and routes to its own OpenAI-compatible base URL, API path,
credential, upstream model, and optional generation defaults. Unknown model keys and thinking
modes not allowed by a route return `INVALID_REQUEST` without contacting an upstream URL.

```dotenv
LLM_MODEL_ROUTES={"general":{"base_url":"http://llm-a:1234","model":"company/general-31b","ssl_verify":false,"allowed_thinking_modes":["default","none"]},"reasoning":{"base_url":"https://llm-b.internal","api_key":"server-secret","model":"company/reasoning-32b","reasoning_effort":"medium"}}
LLM_DEFAULT_ROUTE=general
```

Supported per-route fields are `base_url`, `api_path`, `api_key`, `ssl_verify`, `model`,
`timeout_seconds`, `temperature`, `top_p`, `max_tokens`, `reasoning_effort`, and
`allowed_thinking_modes`. Keep API keys in deployment secrets rather than source control.
