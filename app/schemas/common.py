from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorResponse(BaseModel):
    request_id: str
    error_code: str
    message: str
    details: dict = Field(default_factory=dict)


class PageResponse(BaseModel, Generic[T]):
    items: list[T]
    page: int = 1
    page_size: int = 20
    total: int = 0

