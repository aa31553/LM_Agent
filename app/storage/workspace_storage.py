import json
import shutil
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.config import settings


class WorkspaceStorage:
    """Path-safe storage for persistent workspace data."""

    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or settings.local_storage_root) / "workspaces"

    async def save_original_upload(
        self,
        workspace_id: UUID,
        file_id: UUID,
        filename: str,
        upload: Any,
        *,
        max_bytes: int,
        chunk_size: int = 1024 * 1024,
    ) -> tuple[str, int]:
        path = self._scoped_path(
            workspace_id,
            "files",
            file_id,
            "original",
            self._safe_filename(filename),
        )
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
                        raise ValueError(f"Upload exceeds the {max_bytes}-byte limit.")
                    handle.write(chunk)
        except Exception:
            path.unlink(missing_ok=True)
            self._remove_empty_parents(path.parent, self.workspace_root(workspace_id))
            raise
        return str(path), received

    def save_file_metadata(
        self,
        workspace_id: UUID,
        file_id: UUID,
        payload: dict,
    ) -> str:
        path = self._scoped_path(
            workspace_id,
            "files",
            file_id,
            "metadata.json",
        )
        self._write_json(path, payload)
        return str(path)

    def save_profile(
        self,
        workspace_id: UUID,
        file_id: UUID,
        payload: dict,
    ) -> str:
        path = self._scoped_path(
            workspace_id,
            "files",
            file_id,
            "profile",
            "profile.json",
        )
        self._write_json(path, payload)
        return str(path)

    def llm_content_path(
        self,
        workspace_id: UUID,
        file_id: UUID,
    ) -> Path:
        return self._scoped_path(
            workspace_id,
            "files",
            file_id,
            "llm",
            "content.md",
        )

    def save_llm_content(
        self,
        workspace_id: UUID,
        file_id: UUID,
        content: str,
    ) -> str:
        path = self.llm_content_path(workspace_id, file_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".md.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
        return str(path)

    def read_text_segment(
        self,
        raw_path: str,
        *,
        offset: int,
        max_chars: int,
    ) -> dict[str, Any] | None:
        path = self._validate_existing_file(raw_path)
        if path is None or offset < 0 or max_chars < 1:
            return None
        with path.open("r", encoding="utf-8") as handle:
            handle.seek(offset)
            content = handle.read(max_chars)
            next_offset = handle.tell()
            eof = handle.read(1) == ""
        return {
            "content": content,
            "offset": offset,
            "next_offset": None if eof else next_offset,
            "eof": eof,
            "total_size_bytes": path.stat().st_size,
        }

    def dataset_path(
        self,
        workspace_id: UUID,
        file_id: UUID,
        dataset_key: str,
    ) -> Path:
        return self._scoped_path(
            workspace_id,
            "files",
            file_id,
            "datasets",
            f"{self._safe_dataset_key(dataset_key)}.parquet",
        )

    def save_result(
        self,
        workspace_id: UUID,
        job_id: UUID,
        payload: dict,
    ) -> str:
        path = self._scoped_path(
            workspace_id,
            "jobs",
            job_id,
            "results",
            "result.json",
        )
        self._write_json(path, payload)
        return str(path)

    def artifact_path(
        self,
        workspace_id: UUID,
        artifact_id: UUID,
        filename: str,
    ) -> Path:
        return self._scoped_path(
            workspace_id,
            "artifacts",
            artifact_id,
            self._safe_filename(filename),
        )

    def write_artifact_bytes(
        self,
        workspace_id: UUID,
        artifact_id: UUID,
        filename: str,
        payload: bytes,
    ) -> str:
        path = self.artifact_path(workspace_id, artifact_id, filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_bytes(payload)
        temporary.replace(path)
        return str(path)

    def resolve_artifact(self, raw_path: str) -> Path | None:
        return self._validate_existing_file(raw_path)

    def load_json(self, raw_path: str) -> dict | None:
        path = self._validate_existing_file(raw_path)
        if path is None:
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def delete_file_tree(self, workspace_id: UUID, file_id: UUID) -> bool:
        return self._delete_tree(
            self._scoped_path(workspace_id, "files", file_id),
            self.workspace_root(workspace_id),
        )

    def delete_job_tree(self, workspace_id: UUID, job_id: UUID) -> bool:
        return self._delete_tree(
            self._scoped_path(workspace_id, "jobs", job_id),
            self.workspace_root(workspace_id),
        )

    def delete_artifact_tree(self, workspace_id: UUID, artifact_id: UUID) -> bool:
        return self._delete_tree(
            self._scoped_path(workspace_id, "artifacts", artifact_id),
            self.workspace_root(workspace_id),
        )

    def delete_workspace_tree(self, workspace_id: UUID) -> bool:
        return self._delete_tree(
            self.workspace_root(workspace_id),
            self.root.resolve(),
        )

    def workspace_root(self, workspace_id: UUID) -> Path:
        return self._scoped_path(workspace_id)

    def _scoped_path(self, workspace_id: UUID, *parts: object) -> Path:
        base = self.root.resolve()
        path = base / str(workspace_id)
        for part in parts:
            value = str(part)
            if value in {"", ".", ".."} or Path(value).name != value:
                raise ValueError("Invalid workspace storage path component.")
            path /= value
        resolved = path.resolve()
        if not resolved.is_relative_to(base):
            raise ValueError("Workspace storage path escapes the configured root.")
        return resolved

    def _validate_existing_file(self, raw_path: str) -> Path | None:
        path = Path(raw_path).resolve()
        root = self.root.resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return None
        return path

    @staticmethod
    def _safe_filename(filename: str) -> str:
        safe = Path(filename).name
        if safe in {"", ".", ".."}:
            raise ValueError("A valid filename is required.")
        return safe

    @staticmethod
    def _safe_dataset_key(value: str) -> str:
        safe = "".join(character if character.isalnum() else "_" for character in value)
        safe = safe.strip("_")[:96]
        if not safe:
            raise ValueError("A valid dataset key is required.")
        return safe

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        temporary.replace(path)

    @staticmethod
    def _delete_tree(path: Path, allowed_parent: Path) -> bool:
        resolved = path.resolve()
        parent = allowed_parent.resolve()
        if resolved == parent or not resolved.is_relative_to(parent):
            return False
        if not resolved.is_dir():
            return False
        try:
            shutil.rmtree(resolved)
        except OSError:
            return False
        return True

    @staticmethod
    def _remove_empty_parents(path: Path, stop: Path) -> None:
        while path != stop and path.is_dir():
            try:
                path.rmdir()
            except OSError:
                break
            path = path.parent
