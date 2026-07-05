from app.integrations.embedding_client import EmbeddingClient


class EmbeddingService:
    def __init__(self, client: EmbeddingClient | None = None) -> None:
        self.client = client or EmbeddingClient()

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return await self.client.embed(texts)

