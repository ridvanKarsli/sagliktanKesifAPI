from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from .....container import Container
from .....domain.entities import Role, User
from ...deps import current_user, get_container, require_role

router = APIRouter(tags=["hastalık sözlüğü"])


class ResolveIn(BaseModel):
    name: str = Field(min_length=2, max_length=300)


@router.get("/catalog/status")
def catalog_status(_: User = Depends(current_user), c: Container = Depends(get_container)):
    return c.catalog_service.status()


@router.post("/catalog/build")
def catalog_build(user: User = Depends(require_role(Role.ANALYST)), c: Container = Depends(get_container)):
    return c.catalog_service.build(user)


@router.get("/catalog/search")
def catalog_search(q: str = Query("", max_length=200), limit: int = Query(12, ge=1, le=100), _: User = Depends(current_user),
                   c: Container = Depends(get_container)):
    return c.catalog_service.search(q, limit)


@router.get("/catalog/list")
def catalog_list(q: str = Query("", max_length=200), category: str = Query("", max_length=50), offset: int = Query(0, ge=0),
                 limit: int = Query(200, ge=1, le=1000), _: User = Depends(current_user), c: Container = Depends(get_container)):
    return c.catalog_service.browse(q, category, offset, limit)


@router.get("/catalog/count")
def catalog_count(term: str = Query(..., max_length=300), entries: str = Query("", max_length=4000), _: User = Depends(current_user),
                  c: Container = Depends(get_container)):
    return c.catalog_service.article_count(term, [e for e in entries.split("|") if e])


@router.post("/catalog/resolve")
def catalog_resolve(body: ResolveIn, _: User = Depends(current_user), c: Container = Depends(get_container)):
    return c.catalog_service.resolve(body.name)
