from dataclasses import dataclass

from app.core.config import LLMModelRouteSettings, settings
from app.core.constants import ErrorCode, ThinkingMode
from app.core.exceptions import APIError


@dataclass(frozen=True)
class ResolvedLLMRoute:
    selection: str
    thinking_mode: str
    base_url: str
    api_path: str
    api_key: str
    ssl_verify: bool
    model: str
    timeout_seconds: int
    temperature: float
    top_p: float
    max_tokens: int
    reasoning_effort: str


class LLMRoutingService:
    """Resolve frontend choices only against server-managed LLM routes."""

    def resolve(
        self,
        model: str | None = None,
        thinking_mode: ThinkingMode = ThinkingMode.DEFAULT,
    ) -> ResolvedLLMRoute:
        selection = (model or settings.llm_default_route).strip()
        route = settings.llm_model_routes.get(selection) if selection else None

        if route is not None:
            return self._configured_route(selection, route, thinking_mode)

        if selection and selection != settings.llm_model:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "The selected LLM model is not configured.",
                status_code=400,
                details={
                    "model": selection,
                    "available_models": sorted(settings.llm_model_routes),
                },
            )

        reasoning_effort = self._reasoning_effort(
            thinking_mode=thinking_mode,
            default=settings.llm_reasoning_effort,
            allowed=settings.llm_allowed_thinking_modes,
            selection=settings.llm_model,
        )
        return ResolvedLLMRoute(
            selection=selection or settings.llm_model,
            thinking_mode=thinking_mode.value,
            base_url=settings.llm_base_url,
            api_path=settings.llm_api_path,
            api_key=settings.llm_api_key,
            ssl_verify=settings.llm_ssl_verify,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
            temperature=settings.llm_temperature,
            top_p=settings.llm_top_p,
            max_tokens=settings.llm_max_tokens,
            reasoning_effort=reasoning_effort,
        )

    def _configured_route(
        self,
        selection: str,
        route: LLMModelRouteSettings,
        thinking_mode: ThinkingMode,
    ) -> ResolvedLLMRoute:
        reasoning_effort = self._reasoning_effort(
            thinking_mode=thinking_mode,
            default=route.reasoning_effort,
            allowed=route.allowed_thinking_modes,
            selection=selection,
        )
        return ResolvedLLMRoute(
            selection=selection,
            thinking_mode=thinking_mode.value,
            base_url=route.base_url,
            api_path=route.api_path,
            api_key=route.api_key,
            ssl_verify=route.ssl_verify,
            model=route.model,
            timeout_seconds=route.timeout_seconds or settings.llm_timeout_seconds,
            temperature=(
                route.temperature
                if route.temperature is not None
                else settings.llm_temperature
            ),
            top_p=route.top_p if route.top_p is not None else settings.llm_top_p,
            max_tokens=route.max_tokens or settings.llm_max_tokens,
            reasoning_effort=reasoning_effort,
        )

    def _reasoning_effort(
        self,
        *,
        thinking_mode: ThinkingMode,
        default: str,
        allowed: list[str],
        selection: str,
    ) -> str:
        allowed_values = {value.strip().lower() for value in allowed}
        if thinking_mode.value not in allowed_values:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "The selected thinking mode is not supported by this model.",
                status_code=400,
                details={
                    "model": selection,
                    "thinking_mode": thinking_mode.value,
                    "available_thinking_modes": sorted(allowed_values),
                },
            )
        if thinking_mode == ThinkingMode.DEFAULT:
            value = default.strip()
            return "" if value.lower() == "none" else value
        if thinking_mode == ThinkingMode.NONE:
            return ""
        return thinking_mode.value
