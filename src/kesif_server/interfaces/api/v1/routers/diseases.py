from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import HTMLResponse, Response

from .....container import Container
from .....domain.entities import CandidateFilter, Page, Role, User
from ...deps import current_user, get_container, require_role
from ...schemas.candidates import BridgeOut, CandidateDetailOut, CandidateOut
from ...schemas.common import PageMeta, Paginated
from ...schemas.diseases import DiseaseCreateIn, DiseaseDetailOut, DiseaseOut, RemoveOut, RerankIn, RunCreateIn, RunOut, SettingsOut

router = APIRouter(tags=["hastalıklar"])


def _csv(value: Optional[str]) -> Optional[List[str]]:
    return [v for v in value.split(",") if v] if value else None


@router.get("/diseases", response_model=List[DiseaseDetailOut])
def list_diseases(_: User = Depends(current_user), c: Container = Depends(get_container)):
    return [DiseaseDetailOut.from_view(v) for v in c.list_diseases()]


@router.post("/diseases", response_model=DiseaseOut, status_code=status.HTTP_201_CREATED)
def add_disease(body: DiseaseCreateIn, user: User = Depends(require_role(Role.ANALYST)), c: Container = Depends(get_container)):
    d = c.add_disease(user, name_en=body.name_en, key=body.key, label=body.label, mesh_id=body.mesh_id, mesh_term=body.mesh_term,
                      pubmed_query=body.pubmed_query)
    return DiseaseOut.from_entity(d)


@router.get("/diseases/{key}", response_model=DiseaseDetailOut)
def get_disease(key: str, _: User = Depends(current_user), c: Container = Depends(get_container)):
    return DiseaseDetailOut.from_view(c.get_disease(key))


@router.delete("/diseases/{key}", response_model=RemoveOut)
def remove_disease(key: str, purge: bool = False, user: User = Depends(require_role(Role.ANALYST)), c: Container = Depends(get_container)):
    return RemoveOut(**c.remove_disease(user, key, purge=purge))


# ---- adaylar ----

@router.get("/diseases/{key}/candidates", response_model=Paginated[CandidateOut])
def candidates(
    key: str,
    q: Optional[str] = Query(None, max_length=200, description="ad, eş ad, mekanizma ya da köprü gende arama"),
    kind: Optional[str] = Query(None, description="virgülle: ilaç,besin/takviye,endojen,tanı yardımcısı"),
    show_endogenous: bool = False,
    status: Optional[str] = Query(None, description="virgülle: yeni,literatürde anılmış,tedavi olarak çalışılmış"),
    direction: Optional[str] = None,
    harm: Optional[str] = None,
    durum: Optional[str] = None,
    min_phase: Optional[float] = Query(None, ge=0, le=4),
    min_score: Optional[float] = None,
    exclude_known: bool = False,
    sort: str = Query("score", pattern="^(score|lit_score|mech_score|n_direct_a|max_phase|n_bridges|rank|chemical)$"),
    order: Optional[str] = Query(None, pattern="^(asc|desc)$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    _: User = Depends(current_user),
    c: Container = Depends(get_container),
):
    flt = CandidateFilter(kinds=_csv(kind), show_endogenous=show_endogenous, statuses=_csv(status), directions=_csv(direction),
                          harm_signals=_csv(harm), durum=_csv(durum), min_phase=min_phase, min_score=min_score,
                          exclude_known=exclude_known, text=q, sort=sort, descending=None if order is None else order == "desc")
    total, rows = c.query_candidates(key, flt, Page(offset, limit))
    return Paginated(items=[CandidateOut.from_entity(r) for r in rows], page=PageMeta(total=total, offset=offset, limit=limit))


@router.get("/diseases/{key}/candidates/{chemical_id}", response_model=CandidateDetailOut)
def candidate_detail(key: str, chemical_id: str, _: User = Depends(current_user), c: Container = Depends(get_container)):
    data = c.get_candidate(key, chemical_id)
    return CandidateDetailOut(candidate=CandidateOut.from_entity(data["candidate"]), evidence=data["evidence"])


@router.get("/diseases/{key}/candidates/{chemical_id}/evidence")
def evidence(key: str, chemical_id: str, _: User = Depends(current_user), c: Container = Depends(get_container)):
    return c.results.evidence(key, chemical_id)


@router.get("/diseases/{key}/known")
def known(key: str, _: User = Depends(current_user), c: Container = Depends(get_container)):
    return c.results.known(key)


@router.get("/diseases/{key}/bridges", response_model=List[BridgeOut])
def bridges(key: str, _: User = Depends(current_user), c: Container = Depends(get_container)):
    return c.results.bridges(key)


@router.get("/diseases/{key}/backtest")
def backtest(key: str, _: User = Depends(current_user), c: Container = Depends(get_container)):
    return c.results.backtest(key)


@router.get("/diseases/{key}/tuning")
def tuning(key: str, _: User = Depends(current_user), c: Container = Depends(get_container)):
    return c.results.tuning(key)


@router.get("/diseases/{key}/discovery")
def discovery(key: str, _: User = Depends(current_user), c: Container = Depends(get_container)):
    return c.results.discovery(key)


@router.get("/diseases/{key}/report", response_class=HTMLResponse)
def report(key: str, _: User = Depends(current_user), c: Container = Depends(get_container)):
    return HTMLResponse(c.results.report_html(key))


@router.get("/diseases/{key}/export.csv")
def export_csv(key: str, _: User = Depends(current_user), c: Container = Depends(get_container)):
    return Response(c.results.export_csv(key), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{key}_candidates.csv"'})


@router.get("/diseases/{key}/log")
def log_tail(key: str, tail: int = Query(200, ge=1, le=5000), _: User = Depends(current_user), c: Container = Depends(get_container)):
    return {"lines": c.results.log_tail(key, tail)}


# ---- analiz ----

@router.post("/diseases/{key}/runs", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
def start_run(key: str, body: RunCreateIn, user: User = Depends(require_role(Role.ANALYST)), c: Container = Depends(get_container)):
    run = c.start_analysis(user, key, [s.value for s in body.steps], body.params, body.options())
    return RunOut.from_entity(run)


@router.get("/diseases/{key}/runs", response_model=List[RunOut])
def disease_runs(key: str, limit: int = Query(20, ge=1, le=200), _: User = Depends(current_user), c: Container = Depends(get_container)):
    c.get_disease(key)
    return [RunOut.from_entity(r) for r in c.list_runs(key, limit)]


@router.post("/diseases/{key}/rerank")
def rerank(key: str, body: RerankIn, user: User = Depends(require_role(Role.ANALYST)), c: Container = Depends(get_container)):
    return c.rerank(user, key, body.params, body.cutoff)


@router.get("/diseases/{key}/settings", response_model=SettingsOut)
def get_settings(key: str, _: User = Depends(current_user), c: Container = Depends(get_container)):
    return SettingsOut(**c.get_settings(key))


@router.put("/diseases/{key}/settings")
def put_settings(key: str, body: dict, user: User = Depends(require_role(Role.ANALYST)), c: Container = Depends(get_container)):
    return c.update_settings(user, key, body)


@router.get("/diseases/{key}/estimate")
def estimate(key: str, max_a: int = Query(25000, ge=100, le=500000), _: User = Depends(current_user), c: Container = Depends(get_container)):
    return c.external.estimate(key, max_a)


@router.get("/diseases/{key}/pubmed-count")
def pubmed_count(key: str, _: User = Depends(current_user), c: Container = Depends(get_container)):
    return c.external.pubmed_count(key)
