"""Typed Analysis Intent generation with deterministic compiler validation."""

import dataclasses
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from app.core.exceptions import APIError
from app.schemas.analysis_recipe import IntentDraft
from app.services.analysis_intent_semantic_service import AnalysisIntentSemanticService
from app.services.analysis_recipe_compiler import AnalysisRecipeCompiler
from app.services.llm_service import LLMService
from app.utils.llm_usage import parse_llm_usage, record_llm_usage


@dataclass
class AnalysisIntentDependencies:
    compiler: AnalysisRecipeCompiler
    semantic_service: AnalysisIntentSemanticService
    manifests: dict[str, dict[str, Any]]
    allowed_file_ids: set[str]
    question: str


class PydanticAIAnalysisIntentService:
    """Generate only a typed intent; execution remains behind the recipe compiler."""

    def __init__(
        self,
        llm_service: LLMService,
        *,
        compiler: AnalysisRecipeCompiler,
        semantic_service: AnalysisIntentSemanticService,
        retries: int = 2,
    ) -> None:
        self.llm_service = llm_service
        self.compiler = compiler
        self.semantic_service = semantic_service
        self.retries = max(0, min(retries, 3))

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        question: str,
        manifests: dict[str, dict[str, Any]],
        allowed_file_ids: set[str],
    ) -> IntentDraft:
        route = self.llm_service.route
        completion_root = route.api_path.removesuffix("/chat/completions").rstrip("/")
        base_url = (
            f"{route.base_url.rstrip('/')}/{completion_root.strip('/')}"
            if completion_root
            else route.base_url.rstrip("/")
        )
        deps = AnalysisIntentDependencies(
            compiler=self.compiler,
            semantic_service=self.semantic_service,
            manifests=manifests,
            allowed_file_ids=allowed_file_ids,
            question=question,
        )
        async with httpx.AsyncClient(
            verify=route.ssl_verify,
            timeout=route.timeout_seconds,
            trust_env=False,
        ) as http_client:
            provider = OpenAIProvider(
                base_url=base_url,
                api_key=route.api_key or "lm-agent",
                http_client=http_client,
            )
            agent = Agent(
                OpenAIChatModel(route.model, provider=provider),
                deps_type=AnalysisIntentDependencies,
                output_type=IntentDraft,
                instructions=system_prompt,
                retries=self.retries,
                name="lm-agent-analysis-intent",
            )

            @agent.output_validator
            async def validate_intent(
                ctx: RunContext[AnalysisIntentDependencies],
                intent: IntentDraft,
            ) -> IntentDraft:
                try:
                    _, plan, _, _ = ctx.deps.compiler.compile(
                        intent,
                        ctx.deps.manifests,
                        allowed_file_ids=ctx.deps.allowed_file_ids,
                        plan_origin="llm_intent",
                    )
                    ctx.deps.semantic_service.validate_explicit(
                        ctx.deps.question,
                        plan,
                    )
                except (APIError, ValueError) as exc:
                    raise ModelRetry(str(exc)) from exc
                return intent

            result = await agent.run(user_prompt, deps=deps)
            usage = result.usage
            payload = (
                usage.model_dump()
                if hasattr(usage, "model_dump")
                else dataclasses.asdict(usage)
                if dataclasses.is_dataclass(usage)
                else {}
            )
            record_llm_usage(parse_llm_usage(payload))
            return result.output
