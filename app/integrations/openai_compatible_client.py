import base64
import json
import mimetypes
from pathlib import Path
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.config import settings
from app.core.constants import ErrorCode
from app.core.exceptions import APIError


class OpenAICompatibleClient:
    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self.http_client = http_client

    async def chat_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        image_paths: list[str] | None = None,
    ) -> str:
        payload = {
            "model": settings.llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": self._user_content(user_prompt, image_paths or [])},
            ],
            "temperature": settings.llm_temperature,
            "top_p": settings.llm_top_p,
            "max_tokens": settings.llm_max_tokens,
        }
        if settings.llm_reasoning_effort:
            payload["reasoning_effort"] = settings.llm_reasoning_effort
        headers = self._headers()
        url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"

        try:
            if self.http_client is not None:
                response = await self.http_client.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=settings.llm_timeout_seconds,
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        url,
                        json=payload,
                        headers=headers,
                        timeout=settings.llm_timeout_seconds,
                    )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as exc:
            raise APIError(
                ErrorCode.LLM_SERVICE_ERROR,
                "LLM service returned an error.",
                status_code=502,
                details={"status_code": exc.response.status_code, "body": exc.response.text},
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise APIError(
                ErrorCode.LLM_SERVICE_ERROR,
                "LLM service request failed.",
                status_code=502,
                details={"error": str(exc)},
            ) from exc

        return self._parse_content(body)

    async def stream_chat_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        image_paths: list[str] | None = None,
    ) -> AsyncIterator[str]:
        payload = {
            "model": settings.llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": self._user_content(user_prompt, image_paths or [])},
            ],
            "temperature": settings.llm_temperature,
            "top_p": settings.llm_top_p,
            "max_tokens": settings.llm_max_tokens,
            "stream": True,
        }
        if settings.llm_reasoning_effort:
            payload["reasoning_effort"] = settings.llm_reasoning_effort
        headers = self._headers()
        url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"

        try:
            if self.http_client is not None:
                async with self.http_client.stream(
                    "POST",
                    url,
                    json=payload,
                    headers=headers,
                    timeout=settings.llm_timeout_seconds,
                ) as response:
                    response.raise_for_status()
                    async for chunk in self._iter_stream_content(response):
                        yield chunk
            else:
                async with httpx.AsyncClient() as client:
                    async with client.stream(
                        "POST",
                        url,
                        json=payload,
                        headers=headers,
                        timeout=settings.llm_timeout_seconds,
                    ) as response:
                        response.raise_for_status()
                        async for chunk in self._iter_stream_content(response):
                            yield chunk
        except httpx.HTTPStatusError as exc:
            body = await exc.response.aread()
            raise APIError(
                ErrorCode.LLM_SERVICE_ERROR,
                "LLM service returned an error.",
                status_code=502,
                details={"status_code": exc.response.status_code, "body": body.decode("utf-8", "replace")},
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise APIError(
                ErrorCode.LLM_SERVICE_ERROR,
                "LLM service request failed.",
                status_code=502,
                details={"error": str(exc)},
            ) from exc

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if settings.llm_api_key:
            headers["Authorization"] = f"Bearer {settings.llm_api_key}"
        return headers

    def _parse_content(self, body: dict[str, Any]) -> str:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise APIError(
                ErrorCode.LLM_SERVICE_ERROR,
                "LLM service response does not contain choices.",
                status_code=502,
                details={"body": body},
            )

        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise APIError(
                ErrorCode.LLM_SERVICE_ERROR,
                "LLM service response does not contain a message.",
                status_code=502,
                details={"body": body},
            )

        content = message.get("content")
        if not isinstance(content, str):
            raise APIError(
                ErrorCode.LLM_SERVICE_ERROR,
                "LLM service response message does not contain text content.",
                status_code=502,
                details={"body": body},
            )
        return content

    def _user_content(self, user_prompt: str, image_paths: list[str]) -> str | list[dict]:
        if not settings.llm_send_images_to_model or not image_paths:
            return user_prompt

        content: list[dict] = [{"type": "text", "text": user_prompt}]
        for image_path in image_paths:
            data_url = self._image_data_url(image_path)
            if data_url is None:
                continue
            content.append({"type": "image_url", "image_url": {"url": data_url}})
        return content if len(content) > 1 else user_prompt

    def _image_data_url(self, image_path: str) -> str | None:
        path = Path(image_path)
        if not path.exists() or not path.is_file():
            return None
        mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    async def _iter_stream_content(self, response: httpx.Response) -> AsyncIterator[str]:
        async for line in response.aiter_lines():
            if not line:
                continue
            if line.startswith("data:"):
                line = line[5:].strip()
            if line == "[DONE]":
                break
            try:
                body = json.loads(line)
            except json.JSONDecodeError:
                continue
            choices = body.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta")
            if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                yield delta["content"]
                continue
            message = choice.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                yield message["content"]
