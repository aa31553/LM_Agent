class FileServerClient:
    async def upload(self, file_path: str) -> str:
        raise NotImplementedError("File server integration is reserved for a future storage backend.")

