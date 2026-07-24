"""OpenAI-compatible local embedding service."""

from services.embedding_service.app import create_app

__all__ = ["create_app"]
