# Chat LLM model routing

The four chat endpoints accept the same optional LLM selection fields:

- `POST /api/v1/chat/query`
- `POST /api/v1/chat/stream`
- `POST /api/v1/code-chat/query`
- `POST /api/v1/code-chat/stream`

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
