from app.core.config import settings
from app.integrations.embedding_client import EmbeddingClient


class EmbeddingService:
    def __init__(self, client: EmbeddingClient | None = None) -> None:
        self.client = client or EmbeddingClient()

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        batch_size = max(1, settings.embedding_batch_size)
        for start in range(0, len(texts), batch_size):
            vectors.extend(await self.client.embed(texts[start : start + batch_size]))
        return vectors
