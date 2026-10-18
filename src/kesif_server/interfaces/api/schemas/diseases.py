from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ....application.use_cases.diseases import DiseaseView
from ....domain.entities import AnalysisRun, AnalysisStep, Disease, RunStatus


class DiseaseCreateIn(BaseModel):
    name_en: str = Field(min_length=2, max_length=300)
    key: Optional[str] = Field(default=None, max_length=120)
    label: Optional[str] = Field(default=None, max_length=300)
    mesh_id: str = Field(default="", max_length=40)
    mesh_term: str = Field(default="", max_length=300)
    pubmed_query: str = Field(default="", max_length=4000)


class RunProgressOut(BaseModel):
    step: Optional[str] = None
    message: str = ""
    done: Optional[int] = None
    total: Optional[int] = None
    completed_steps: List[str] = Field(default_factory=list)


class RunOut(BaseModel):
    id: str
    disease_key: str
    steps: List[AnalysisStep]
    status: RunStatus
    queue_position: int
    progress: RunProgressOut
    error: Optional[str] = None
    summary: Optional[Dict[str, Any]] = None
    attempts: int
    params: Dict[str, Any]
    options: Dict[str, Any]
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    @classmethod
    def from_entity(cls, r: AnalysisRun) -> RunOut:
        return cls(id=r.id, disease_key=r.disease_key, steps=r.steps, status=r.status, queue_position=r.queue_position,
                   progress=RunProgressOut(**r.progress.to_dict()), error=r.error, summary=r.summary, attempts=r.attempts,
                   params=r.params, options=r.options.to_dict(), created_by=r.created_by, created_at=r.created_at,
                   started_at=r.started_at, finished_at=r.finished_at)


class DiseaseOut(BaseModel):
    key: str
    label: str
    name_en: str
    mesh_id: str
    pubmed_query: str
    is_default: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_entity(cls, d: Disease) -> DiseaseOut:
        return cls(key=d.key, label=d.label, name_en=d.name_en, mesh_id=d.mesh_id, pubmed_query=d.pubmed_query,
                   is_default=d.is_default, created_at=d.created_at, updated_at=d.updated_at)


class DiseaseDetailOut(DiseaseOut):
    status: Dict[str, Any]
    active_run: Optional[RunOut] = None
    n_candidates: int = 0

    @classmethod
    def from_view(cls, v: DiseaseView) -> DiseaseDetailOut:
        base = DiseaseOut.from_entity(v.disease).model_dump()
        return cls(**base, status=v.status, active_run=RunOut.from_entity(v.active_run) if v.active_run else None, n_candidates=v.n_candidates)


class RemoveOut(BaseModel):
    removed: str
    purged: List[str]


class RunCreateIn(BaseModel):
    steps: List[AnalysisStep] = Field(min_length=1)
    params: Optional[Dict[str, Any]] = None
    cutoff: int = Field(default=2018, ge=1990, le=2100)
    cutoff_a: int = Field(default=2016, ge=1990, le=2100)
    cutoff_b: int = Field(default=2019, ge=1990, le=2100)
    top: int = Field(default=100, ge=10, le=1000)
    start_year: int = Field(default=1990, ge=1900, le=2100)
    max_a: Optional[int] = Field(default=25000, ge=100, le=500000)
    per_gene: int = Field(default=300, ge=50, le=2000)
    force: bool = False

    def options(self) -> Dict[str, Any]:
        return self.model_dump(exclude={"steps", "params"})


class RerankIn(BaseModel):
    params: Dict[str, Any]
    cutoff: Optional[int] = Field(default=None, ge=1990, le=2100)


class SettingsOut(BaseModel):
    params: Dict[str, Any]
    defaults: Dict[str, Any]
