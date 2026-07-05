from typing import Protocol


class FileStorage(Protocol):
    async def save(self, filename: str, content: bytes) -> str:
        ...

    async def open(self, file_path: str) -> bytes:
        ...

