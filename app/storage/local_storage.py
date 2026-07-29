from pathlib import Path
from typing import Any

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

    async def save_upload(
        self,
        category: str,
        filename: str,
        upload: Any,
        *,
        max_bytes: int,
        chunk_size: int = 1024 * 1024,
    ) -> tuple[str, int]:
        path = self._artifact_path(category, filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        received = 0
        try:
            with path.open("wb") as handle:
                while True:
                    chunk = await upload.read(chunk_size)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > max_bytes:
                        raise ValueError(
                            f"Upload exceeds the {max_bytes}-byte limit."
                        )
                    handle.write(chunk)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return str(path), received

    def delete_artifact(self, raw_path: str) -> bool:
        path = Path(raw_path).resolve()
        root = self.root.resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return False
        try:
            path.unlink()
        except OSError:
            return False
        parent = path.parent
        while parent != root and parent.is_dir():
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
        return True

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
