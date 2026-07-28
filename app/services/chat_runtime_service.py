import asyncio
from contextlib import asynccontextmanager

from app.core.config import settings
from app.core.constants import ErrorCode
from app.core.exceptions import APIError


class ChatRuntimeService:
    """Bound concurrent chat work and apply one end-to-end request deadline."""

    def __init__(self) -> None:
        self._limit: int | None = None
        self._semaphore: asyncio.Semaphore | None = None

    def _current_semaphore(self) -> asyncio.Semaphore:
        limit = settings.chat_max_concurrent_requests
        if self._semaphore is None or self._limit != limit:
            self._limit = limit
            self._semaphore = asyncio.Semaphore(limit)
        return self._semaphore

    @asynccontextmanager
    async def request_slot(self):
        semaphore = self._current_semaphore()
        try:
            await asyncio.wait_for(
                semaphore.acquire(),
                timeout=settings.chat_queue_timeout_seconds,
            )
        except TimeoutError as exc:
            raise APIError(
                ErrorCode.CHAT_BUSY,
                "The chat service is busy. Please retry shortly.",
                status_code=503,
                details={
                    "max_concurrent_requests": settings.chat_max_concurrent_requests,
                    "queue_timeout_seconds": settings.chat_queue_timeout_seconds,
                },
            ) from exc

        try:
            try:
                async with asyncio.timeout(settings.chat_request_timeout_seconds):
                    yield
            except TimeoutError as exc:
                raise APIError(
                    ErrorCode.CHAT_TIMEOUT,
                    "The chat request exceeded the configured processing deadline.",
                    status_code=504,
                    details={
                        "timeout_seconds": settings.chat_request_timeout_seconds,
                    },
                ) from exc
        finally:
            semaphore.release()


chat_runtime_service = ChatRuntimeService()
