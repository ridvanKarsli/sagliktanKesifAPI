from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from .....container import Container
from .....domain.entities import Role, User
from ...deps import current_user, get_container, require_role
from ...schemas.diseases import RunOut

router = APIRouter(tags=["analiz işleri"])


@router.get("/runs", response_model=List[RunOut])
def list_runs(disease: Optional[str] = None, limit: int = Query(50, ge=1, le=500), _: User = Depends(current_user),
              c: Container = Depends(get_container)):
    return [RunOut.from_entity(r) for r in c.list_runs(disease, limit)]


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: str, _: User = Depends(current_user), c: Container = Depends(get_container)):
    return RunOut.from_entity(c.get_run(run_id))


@router.delete("/runs/{run_id}", response_model=RunOut)
def cancel_run(run_id: str, user: User = Depends(require_role(Role.ANALYST)), c: Container = Depends(get_container)):
    return RunOut.from_entity(c.cancel_analysis(user, run_id))
