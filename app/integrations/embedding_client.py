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

        payload = {
            "model": settings.embedding_model,
            "input": texts,
        }
        headers = self._headers()

        try:
            if self.http_client is not None:
                response = await self.http_client.post(
                    settings.embedding_endpoint,
                    json=payload,
                    headers=headers,
                    timeout=settings.llm_timeout_seconds,
                )
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        settings.embedding_endpoint,
                        json=payload,
                        headers=headers,
                        timeout=settings.llm_timeout_seconds,
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

        vectors = self._parse_embeddings(body)
        if len(vectors) != len(texts):
            raise APIError(
                ErrorCode.EMBEDDING_SERVICE_ERROR,
                "Embedding service returned an unexpected number of vectors.",
                status_code=502,
                details={"expected": len(texts), "actual": len(vectors)},
            )
        return [self._normalize_dimension(vector) for vector in vectors]

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if settings.llm_api_key:
            headers["Authorization"] = f"Bearer {settings.llm_api_key}"
        return headers

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
        if len(vector) > expected:
            return vector[:expected]
        raise APIError(
            ErrorCode.EMBEDDING_SERVICE_ERROR,
            "Embedding service returned a vector with fewer dimensions than configured.",
            status_code=502,
            details={"expected": expected, "actual": len(vector)},
        )
