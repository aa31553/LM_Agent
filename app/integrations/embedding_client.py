from urllib.parse import urlsplit, urlunsplit

import httpx

from app.core.config import settings
from app.core.constants import ErrorCode
from app.core.exceptions import APIError


class EmbeddingClient:
    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self.http_client = http_client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        payload = {"model": settings.embedding_model, "input": texts}
        body = await self._request_json("POST", settings.embedding_endpoint, json=payload)
        vectors = self._parse_embeddings(body)
        if len(vectors) != len(texts):
            raise APIError(
                ErrorCode.EMBEDDING_SERVICE_ERROR,
                "Embedding service returned an unexpected number of vectors.",
                status_code=502,
                details={"expected": len(texts), "actual": len(vectors)},
            )
        return [self._normalize_dimension(vector) for vector in vectors]

    async def status(self) -> dict[str, object]:
        body = await self._request_json("GET", self._service_url("/status"))
        if not isinstance(body, dict):
            raise APIError(
                ErrorCode.EMBEDDING_SERVICE_ERROR,
                "Embedding service returned an invalid status response.",
                status_code=502,
            )
        return body

    async def _request_json(self, method: str, url: str, **kwargs) -> dict:
        try:
            if self.http_client is not None:
                response = await self.http_client.request(
                    method,
                    url,
                    headers=self._headers(),
                    timeout=settings.embedding_timeout_seconds,
                    **kwargs,
                )
            else:
                async with httpx.AsyncClient(verify=settings.embedding_ssl_verify) as client:
                    response = await client.request(
                        method,
                        url,
                        headers=self._headers(),
                        timeout=settings.embedding_timeout_seconds,
                        **kwargs,
                    )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as exc:
            raise APIError(
                ErrorCode.EMBEDDING_SERVICE_ERROR,
                "Embedding service returned an error.",
                status_code=502,
                details={"status_code": exc.response.status_code, "body": exc.response.text},
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise APIError(
                ErrorCode.EMBEDDING_SERVICE_ERROR,
                "Embedding service request failed.",
                status_code=502,
                details={"error": str(exc)},
            ) from exc
        if not isinstance(body, dict):
            raise APIError(
                ErrorCode.EMBEDDING_SERVICE_ERROR,
                "Embedding service response must be a JSON object.",
                status_code=502,
            )
        return body

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if settings.embedding_api_key:
            headers["Authorization"] = f"Bearer {settings.embedding_api_key}"
        return headers

    def _service_url(self, path: str) -> str:
        configured = settings.embedding_service_base_url.strip()
        if configured:
            return f"{configured.rstrip('/')}/{path.lstrip('/')}"

        endpoint = urlsplit(settings.embedding_endpoint)
        endpoint_path = endpoint.path
        suffix = "/v1/embeddings"
        if endpoint_path.endswith(suffix):
            endpoint_path = endpoint_path[: -len(suffix)]
        status_path = f"{endpoint_path.rstrip('/')}/{path.lstrip('/')}"
        return urlunsplit(
            (endpoint.scheme, endpoint.netloc, status_path, "", "")
        )

    def _parse_embeddings(self, body: dict) -> list[list[float]]:
        data = body.get("data")
        if not isinstance(data, list):
            raise APIError(
                ErrorCode.EMBEDDING_SERVICE_ERROR,
                "Embedding service response does not contain data.",
                status_code=502,
                details={"body": body},
            )

        sorted_items = sorted(data, key=lambda item: item.get("index", 0))
        vectors: list[list[float]] = []
        for item in sorted_items:
            embedding = item.get("embedding")
            if not isinstance(embedding, list):
                raise APIError(
                    ErrorCode.EMBEDDING_SERVICE_ERROR,
                    "Embedding service response contains an invalid vector.",
                    status_code=502,
                    details={"item": item},
                )
            vectors.append([float(value) for value in embedding])
        return vectors

    def _normalize_dimension(self, vector: list[float]) -> list[float]:
        expected = settings.embedding_dimension
        if len(vector) == expected:
            return vector
        raise APIError(
            ErrorCode.EMBEDDING_SERVICE_ERROR,
            "Embedding service returned a vector with fewer dimensions than configured.",
            status_code=502,
            details={"expected": expected, "actual": len(vector)},
        )
