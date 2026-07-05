from pathlib import Path

from app.core.config import settings


class LocalStorage:
    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or settings.local_storage_root)

    async def save(self, filename: str, content: bytes) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / filename
        path.write_bytes(content)
        return str(path)

    async def open(self, file_path: str) -> bytes:
        return Path(file_path).read_bytes()

