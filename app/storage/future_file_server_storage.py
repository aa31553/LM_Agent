from app.integrations.file_server_client import FileServerClient


class FutureFileServerStorage:
    def __init__(self, client: FileServerClient | None = None) -> None:
        self.client = client or FileServerClient()

    async def save(self, filename: str, content: bytes) -> str:
        raise NotImplementedError("Remote file server storage is not in MVP scope.")

    async def save_original(self, filename: str, content: bytes) -> str:
        raise NotImplementedError("Remote file server storage is not in MVP scope.")

    async def save_markdown(self, filename: str, content: str) -> str:
        raise NotImplementedError("Remote file server storage is not in MVP scope.")

    async def open(self, file_path: str) -> bytes:
        raise NotImplementedError("Remote file server storage is not in MVP scope.")
