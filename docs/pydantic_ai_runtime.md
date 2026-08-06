# PydanticAI runtime boundary

LM_Agent now has an application-owned runtime boundary at `app/agent_runtime/`.
The existing `AgentToolService` remains the gateway for document retrieval,
Workspace ACL checks, confidentiality filtering, DLP masking, result budgeting,
and audit-compatible tool traces. PydanticAI only controls typed tool selection,
argument validation, retry limits, and the provider request loop.

## Rollout

The default remains the rollback-safe legacy runtime:

```text
AGENT_RUNTIME=legacy
```

An internal canary can enable the typed runtime without changing an API route:

```text
AGENT_RUNTIME=pydantic_ai
AGENT_RUNTIME_RETRIES=1
AGENT_STRUCTURED_OUTPUT=false
```

`AGENT_STRUCTURED_OUTPUT=true` enables the `AnswerOutput` Pydantic model. The
adapter renders it back to the existing Markdown-compatible answer contract so
the public Chat and Code Chat response schemas remain unchanged.

Analysis Intent uses the typed `IntentDraft` output only when both
`AGENT_RUNTIME=pydantic_ai` and `ANALYSIS_INTENT_FLOW_ENABLED=true` are enabled.
Its output validator calls `AnalysisRecipeCompiler` and
`AnalysisIntentSemanticService`; it cannot execute a recipe or bypass the
confirm gate. Workspace files, RAG, DLP, ACL, Workers, DuckDB/Polars, and formal
Audit remain LM_Agent responsibilities.

PydanticAI is intentionally not configured with shell, filesystem, web, MCP,
dynamic tool discovery, or arbitrary code execution capabilities. The dependency
is pinned to `pydantic-ai-slim[openai,retries]==1.107.1`; upgrades must first pass
the adapter and API contract tests in an isolated branch.

## Installation compatibility

LM_Agent uses the PydanticAI 1.107.1 API, including the canonical
`OpenAIChatModel` class. Do not downgrade PydanticAI to 0.x or validate the
installation with the deprecated `OpenAIModel` name.

PydanticAI's full and slim distributions share the `pydantic_ai` import
namespace. After changing between major versions, uninstall both distributions
before reinstalling the project so files from two versions cannot remain mixed:

```bat
cd /d D:\Python\LM_Agent
.venv\Scripts\python.exe -m pip uninstall -y pydantic-ai pydantic-ai-slim pydantic-graph
.venv\Scripts\python.exe -m pip install --no-cache-dir -e .
.venv\Scripts\python.exe -m pip check
.venv\Scripts\python.exe -m app.scripts.validate_pydantic_ai
.venv\Scripts\python.exe -c "from pydantic_ai.models.openai import OpenAIChatModel; print('PydanticAI import OK')"
```

The validation command checks the exact PydanticAI and pydantic-graph versions,
the OpenAI extra, OpenTelemetry API, and the imports used by the runtime. The
private `opentelemetry._events` module is not part of LM_Agent's compatibility
contract and must not be used as an installation check.
