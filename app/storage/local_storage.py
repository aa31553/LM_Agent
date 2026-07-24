from pathlib import Path

from app.core.config import settings


class LocalStorage:
    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or settings.local_storage_root)

    async def save(self, filename: str, content: bytes) -> str:
        return await self.save_original(filename, content)

    async def save_original(self, filename: str, content: bytes) -> str:
        path = self._artifact_path("originals", filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return str(path)

    async def save_markdown(self, filename: str, content: str) -> str:
        path = self._artifact_path("markdown", filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(path)

    async def open(self, file_path: str) -> bytes:
        return Path(file_path).read_bytes()

    def _artifact_path(self, category: str, filename: str) -> Path:
        safe_filename = Path(filename).name
        if safe_filename in {"", ".", ".."}:
            raise ValueError("A valid artifact filename is required.")
        return self.root / category / safe_filename
