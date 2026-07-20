import json
import mimetypes
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import quote
from uuid import UUID, uuid4

import yaml

from app.core.config import settings
from app.core.constants import ErrorCode, PermissionLevel, PermissionSubjectType
from app.core.exceptions import APIError
from app.core.security import Principal


SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FRONTMATTER_PATTERN = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)
TEXT_SUFFIXES = {
    ".css",
    ".csv",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".py",
    ".sql",
    ".svg",
    ".toml",
    ".ts",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
METADATA_FILE = ".lm-agent-skill.json"


@dataclass(frozen=True)
class SkillFileRecord:
    relative_path: str
    path: Path
    size: int
    mime_type: str
    is_text: bool


@dataclass(frozen=True)
class SkillPermissionRecord:
    permission_id: UUID
    subject_type: str
    subject_value: str
    permission: str


@dataclass(frozen=True)
class SkillRecord:
    name: str
    description: str
    instructions: str
    enabled: bool
    is_system: bool
    always_on: bool
    created_at: datetime
    updated_at: datetime
    directory: Path
    files: list[SkillFileRecord]
    permissions: list[SkillPermissionRecord]


@dataclass(frozen=True)
class ResolvedSkillContext:
    prompt: str
    triggered_names: list[str]


class SkillService:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or settings.skills_root)
        self.defaults_root = Path(__file__).resolve().parents[1] / "default_skills"

    def ensure_defaults(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.defaults_root.exists():
            return
        for source in sorted(path for path in self.defaults_root.iterdir() if path.is_dir()):
            target = self.root / source.name
            if not target.exists():
                shutil.copytree(source, target)
            metadata = self._read_metadata(target)
            expected_always_on = source.name == "secure-rag"
            if (
                metadata.get("is_system") is not True
                or "enabled" not in metadata
                or "always_on" not in metadata
            ):
                metadata["is_system"] = True
                metadata.setdefault("enabled", True)
                metadata.setdefault("always_on", expected_always_on)
                self._write_metadata(target, metadata, preserve_created=True)

    def list_skills(self, principal: Principal | None = None) -> list[SkillRecord]:
        self.ensure_defaults()
        records: list[SkillRecord] = []
        for directory in sorted(path for path in self.root.iterdir() if path.is_dir()):
            if (directory / "SKILL.md").is_file():
                record = self._record(directory)
                if principal is None or self.can_use(principal, record):
                    records.append(record)
        return records

    def get(self, name: str, principal: Principal | None = None) -> SkillRecord:
        self.ensure_defaults()
        directory = self._skill_directory(name)
        if not (directory / "SKILL.md").is_file():
            raise APIError(ErrorCode.SKILL_NOT_FOUND, "Skill not found.", 404)
        record = self._record(directory)
        if principal is not None:
            self.ensure_can_use(principal, record)
        return record

    def can_use(self, principal: Principal, record: SkillRecord) -> bool:
        if not principal.is_active:
            return False
        if "admin" in principal.roles:
            return True
        if not record.permissions:
            return True
        for rule in record.permissions:
            if rule.permission not in {PermissionLevel.READ.value, PermissionLevel.ADMIN.value}:
                continue
            if rule.subject_type == PermissionSubjectType.USER.value:
                if rule.subject_value == principal.external_user_id:
                    return True
            elif rule.subject_type == PermissionSubjectType.DEPARTMENT.value:
                if rule.subject_value == principal.department:
                    return True
            elif rule.subject_type == PermissionSubjectType.ROLE.value:
                if rule.subject_value in principal.roles:
                    return True
        return False

    def ensure_can_use(self, principal: Principal, record: SkillRecord) -> None:
        if not self.can_use(principal, record):
            raise APIError(
                ErrorCode.PERMISSION_DENIED,
                "User does not have permission to use this skill.",
                403,
            )

    def create(
        self,
        *,
        name: str,
        description: str,
        instructions: str,
        enabled: bool,
    ) -> SkillRecord:
        self.ensure_defaults()
        self._validate_name(name)
        directory = self._skill_directory(name)
        if directory.exists():
            raise APIError(ErrorCode.INVALID_REQUEST, "A skill with this name already exists.", 409)
        directory.mkdir(parents=True)
        try:
            self._write_skill_file(directory, name, description, instructions)
            self._write_openai_yaml(directory, name, description)
            self._write_metadata(
                directory,
                {"enabled": enabled, "is_system": False, "always_on": False},
            )
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        return self._record(directory)

    def update(
        self,
        name: str,
        *,
        description: str | None = None,
        instructions: str | None = None,
        enabled: bool | None = None,
    ) -> SkillRecord:
        current = self.get(name)
        next_description = description if description is not None else current.description
        next_instructions = instructions if instructions is not None else current.instructions
        if description is not None or instructions is not None:
            self._write_skill_file(current.directory, name, next_description, next_instructions)
            self._write_openai_yaml(current.directory, name, next_description)
        metadata = self._read_metadata(current.directory)
        if enabled is not None:
            metadata["enabled"] = enabled
        self._write_metadata(current.directory, metadata, preserve_created=True)
        return self._record(current.directory)

    def delete(self, name: str) -> None:
        record = self.get(name)
        if record.is_system:
            raise APIError(ErrorCode.INVALID_REQUEST, "System skills cannot be deleted.", 400)
        shutil.rmtree(record.directory)

    def save_file(
        self,
        name: str,
        relative_path: str,
        content: bytes,
        *,
        overwrite: bool,
    ) -> SkillFileRecord:
        record = self.get(name)
        if len(content) > settings.skill_max_file_bytes:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                f"Skill file exceeds the {settings.skill_max_file_bytes}-byte limit.",
                413,
            )
        target = self._safe_file_path(record.directory, relative_path)
        if target.name in {"SKILL.md", METADATA_FILE} and target.parent == record.directory:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Use the skill update endpoint to change SKILL.md.",
                400,
            )
        if target.exists() and not overwrite:
            raise APIError(ErrorCode.INVALID_REQUEST, "Skill file already exists.", 409)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        self._touch(record.directory)
        return self._file_record(record.directory, target)

    def delete_file(self, name: str, relative_path: str) -> None:
        record = self.get(name)
        target = self._safe_file_path(record.directory, relative_path)
        if target.name in {"SKILL.md", METADATA_FILE} and target.parent == record.directory:
            raise APIError(ErrorCode.INVALID_REQUEST, "Required skill files cannot be deleted.", 400)
        if not target.is_file():
            raise APIError(ErrorCode.INVALID_REQUEST, "Skill file not found.", 404)
        target.unlink()
        self._touch(record.directory)

    def resolve_file(
        self,
        name: str,
        relative_path: str,
        principal: Principal | None = None,
    ) -> SkillFileRecord:
        record = self.get(name, principal)
        target = self._safe_file_path(record.directory, relative_path)
        if target.name == METADATA_FILE or not target.is_file():
            raise APIError(ErrorCode.INVALID_REQUEST, "Skill file not found.", 404)
        return self._file_record(record.directory, target)

    def resolve_context(
        self,
        query: str,
        principal: Principal,
        max_chars: int = 12_000,
    ) -> ResolvedSkillContext:
        enabled = [record for record in self.list_skills(principal) if record.enabled]
        query_lower = query.lower()
        query_terms = self._terms(query_lower)
        triggered: list[SkillRecord] = []
        for record in enabled:
            explicit = f"${record.name}" in query_lower
            metadata_terms = self._terms(f"{record.name} {record.description}".lower())
            if record.always_on or explicit or bool(query_terms & metadata_terms):
                triggered.append(record)

        available = "\n".join(f"- ${item.name}: {item.description}" for item in enabled)
        sections = ["## Available Skills", available]
        if triggered:
            instructions = "\n\n".join(
                f"### ${item.name}\n{item.instructions.strip()}" for item in triggered
            )
            sections.extend(["## Active Skill Instructions", instructions])
        prompt = "\n\n".join(part for part in sections if part).strip()
        return ResolvedSkillContext(
            prompt=prompt[:max_chars],
            triggered_names=[item.name for item in triggered],
        )

    def list_permissions(self, name: str) -> list[SkillPermissionRecord]:
        return self.get(name).permissions

    def create_or_update_permission(
        self,
        name: str,
        *,
        subject_type: PermissionSubjectType,
        subject_value: str,
        permission: PermissionLevel,
    ) -> SkillPermissionRecord:
        if permission not in {PermissionLevel.READ, PermissionLevel.ADMIN}:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                "Skill permissions support read or admin access only.",
                400,
            )
        clean_subject = subject_value.strip()
        if not clean_subject:
            raise APIError(ErrorCode.INVALID_REQUEST, "Permission subject is required.", 400)
        record = self.get(name)
        metadata = self._read_metadata(record.directory)
        rules = list(metadata.get("permissions") or [])
        matching = next(
            (
                item
                for item in rules
                if item.get("subject_type") == subject_type.value
                and item.get("subject_value") == clean_subject
            ),
            None,
        )
        if matching is None:
            matching = {
                "permission_id": str(uuid4()),
                "subject_type": subject_type.value,
                "subject_value": clean_subject,
            }
            rules.append(matching)
        matching["permission"] = permission.value
        metadata["permissions"] = rules
        self._write_metadata(record.directory, metadata, preserve_created=True)
        return self._permission_record(matching)

    def delete_permission(self, name: str, permission_id: UUID) -> None:
        record = self.get(name)
        metadata = self._read_metadata(record.directory)
        rules = list(metadata.get("permissions") or [])
        remaining = [item for item in rules if item.get("permission_id") != str(permission_id)]
        if len(remaining) == len(rules):
            raise APIError(ErrorCode.INVALID_REQUEST, "Skill permission not found.", 404)
        metadata["permissions"] = remaining
        self._write_metadata(record.directory, metadata, preserve_created=True)

    def raw_url(self, name: str, relative_path: str) -> str:
        encoded_path = quote(relative_path, safe="/")
        return f"{settings.api_v1_prefix}/skills/{quote(name)}/files/{encoded_path}"

    def _record(self, directory: Path) -> SkillRecord:
        name, description, instructions = self._parse_skill_file(directory / "SKILL.md")
        if name != directory.name:
            raise APIError(
                ErrorCode.INVALID_REQUEST,
                f"Skill directory '{directory.name}' does not match frontmatter name '{name}'.",
                500,
            )
        metadata = self._read_metadata(directory)
        if not metadata:
            self._write_metadata(
                directory,
                {"enabled": True, "is_system": False, "always_on": False},
            )
            metadata = self._read_metadata(directory)
        files = [
            self._file_record(directory, path)
            for path in sorted(directory.rglob("*"))
            if path.is_file() and path.name != METADATA_FILE
        ]
        permissions = [
            self._permission_record(item)
            for item in metadata.get("permissions", [])
            if isinstance(item, dict)
        ]
        return SkillRecord(
            name=name,
            description=description,
            instructions=instructions,
            enabled=bool(metadata.get("enabled", True)),
            is_system=bool(metadata.get("is_system", False)),
            always_on=bool(metadata.get("always_on", False)),
            created_at=self._parse_datetime(metadata.get("created_at")),
            updated_at=self._parse_datetime(metadata.get("updated_at")),
            directory=directory,
            files=files,
            permissions=permissions,
        )

    def _permission_record(self, payload: dict) -> SkillPermissionRecord:
        try:
            permission_id = UUID(str(payload.get("permission_id")))
        except ValueError:
            permission_id = uuid4()
        return SkillPermissionRecord(
            permission_id=permission_id,
            subject_type=str(payload.get("subject_type") or ""),
            subject_value=str(payload.get("subject_value") or ""),
            permission=str(payload.get("permission") or ""),
        )

    def _parse_skill_file(self, path: Path) -> tuple[str, str, str]:
        text = path.read_text(encoding="utf-8")
        match = FRONTMATTER_PATTERN.match(text)
        if match is None:
            raise APIError(ErrorCode.INVALID_REQUEST, f"Invalid SKILL.md frontmatter: {path}", 500)
        metadata = yaml.safe_load(match.group(1))
        if not isinstance(metadata, dict):
            raise APIError(ErrorCode.INVALID_REQUEST, f"Invalid SKILL.md metadata: {path}", 500)
        name = str(metadata.get("name") or "").strip()
        description = str(metadata.get("description") or "").strip()
        self._validate_name(name)
        if not description or len(description) > 1024:
            raise APIError(ErrorCode.INVALID_REQUEST, "Skill description is invalid.", 500)
        return name, description, match.group(2).strip()

    def _write_skill_file(
        self,
        directory: Path,
        name: str,
        description: str,
        instructions: str,
    ) -> None:
        clean_description = description.strip()
        clean_instructions = instructions.strip()
        if not clean_description or len(clean_description) > 1024:
            raise APIError(ErrorCode.INVALID_REQUEST, "Skill description is invalid.", 400)
        if not clean_instructions:
            raise APIError(ErrorCode.INVALID_REQUEST, "Skill instructions are required.", 400)
        frontmatter = yaml.safe_dump(
            {"name": name, "description": clean_description},
            allow_unicode=True,
            sort_keys=False,
        ).strip()
        (directory / "SKILL.md").write_text(
            f"---\n{frontmatter}\n---\n\n{clean_instructions}\n",
            encoding="utf-8",
        )

    def _write_openai_yaml(self, directory: Path, name: str, description: str) -> None:
        agents_dir = directory / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        display_name = " ".join(part.capitalize() for part in name.split("-"))
        payload = {
            "interface": {
                "display_name": display_name,
                "short_description": description[:120],
                "default_prompt": f"Use ${name} to help with this request.",
            }
        }
        (agents_dir / "openai.yaml").write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    def _validate_name(self, name: str) -> None:
        if len(name) > 64 or not SKILL_NAME_PATTERN.fullmatch(name):
            raise APIError(ErrorCode.INVALID_REQUEST, "Invalid skill name.", 400)
        if "anthropic" in name or "claude" in name:
            raise APIError(ErrorCode.INVALID_REQUEST, "Skill name contains a reserved word.", 400)

    def _skill_directory(self, name: str) -> Path:
        self._validate_name(name)
        return self.root / name

    def _safe_file_path(self, directory: Path, relative_path: str) -> Path:
        posix_path = PurePosixPath(relative_path.replace("\\", "/"))
        if posix_path.is_absolute() or not posix_path.parts or ".." in posix_path.parts:
            raise APIError(ErrorCode.INVALID_REQUEST, "Invalid skill file path.", 400)
        target = (directory / Path(*posix_path.parts)).resolve()
        try:
            target.relative_to(directory.resolve())
        except ValueError as exc:
            raise APIError(ErrorCode.INVALID_REQUEST, "Invalid skill file path.", 400) from exc
        return target

    def _file_record(self, directory: Path, path: Path) -> SkillFileRecord:
        relative_path = path.relative_to(directory).as_posix()
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        is_text = mime_type.startswith("text/") or path.suffix.lower() in TEXT_SUFFIXES
        return SkillFileRecord(
            relative_path=relative_path,
            path=path,
            size=path.stat().st_size,
            mime_type=mime_type,
            is_text=is_text,
        )

    def _read_metadata(self, directory: Path) -> dict:
        path = directory / METADATA_FILE
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_metadata(
        self,
        directory: Path,
        metadata: dict,
        *,
        preserve_created: bool = False,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        current = self._read_metadata(directory) if preserve_created else {}
        payload = {**current, **metadata}
        payload["created_at"] = current.get("created_at", now)
        payload["updated_at"] = now
        temporary = directory / f"{METADATA_FILE}.tmp"
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(directory / METADATA_FILE)

    def _touch(self, directory: Path) -> None:
        self._write_metadata(directory, self._read_metadata(directory), preserve_created=True)

    def _parse_datetime(self, value: object) -> datetime:
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return datetime.now(timezone.utc)

    def _terms(self, text: str) -> set[str]:
        return {term for term in re.findall(r"[a-z0-9\u4e00-\u9fff-]+", text) if len(term) >= 3}
