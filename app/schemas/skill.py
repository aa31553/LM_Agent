from datetime import datetime

from pydantic import BaseModel, Field


class SkillCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str = Field(min_length=1, max_length=1024)
    instructions: str = Field(min_length=1)
    enabled: bool = True


class SkillUpdate(BaseModel):
    description: str | None = Field(default=None, min_length=1, max_length=1024)
    instructions: str | None = Field(default=None, min_length=1)
    enabled: bool | None = None


class SkillFileInfo(BaseModel):
    relative_path: str
    size: int
    mime_type: str
    is_text: bool
    raw_url: str


class SkillSummary(BaseModel):
    name: str
    description: str
    enabled: bool
    is_system: bool
    always_on: bool
    skill_file_url: str
    file_count: int
    permission_count: int
    created_at: datetime
    updated_at: datetime


class SkillDetail(SkillSummary):
    instructions: str
    files: list[SkillFileInfo] = Field(default_factory=list)


class SkillListResponse(BaseModel):
    items: list[SkillSummary]


class SkillFileMutationResponse(BaseModel):
    skill_name: str
    file: SkillFileInfo
