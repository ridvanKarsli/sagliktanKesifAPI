"""Ortak API şemaları."""

from __future__ import annotations

from typing import Any, Dict, Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Dict[str, Any] = Field(default_factory=dict)
    request_id: Optional[str] = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class PageMeta(BaseModel):
    total: int
    offset: int
    limit: int


class Paginated(BaseModel, Generic[T]):
    items: List[T]
    page: PageMeta


class OkResponse(BaseModel):
    ok: bool = True
