from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ....domain.entities import Candidate


class CandidateOut(BaseModel):
    rank: int
    chemical_id: str
    chemical: str
    chembl_id: str = ""
    score: float
    lit_score: float
    mech_score: float
    penalty: float
    direction: str
    mechanism: str
    max_phase: Optional[float] = None
    n_direct_a: int
    n_bridges: int
    top_bridges: str
    aliases: str
    kind: str
    status: str
    harm_signal: str
    durum: str
    clinical_trials: Optional[int] = None
    pubmed_direct: Optional[int] = None
    extra: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_entity(cls, c: Candidate) -> CandidateOut:
        return cls(**{k: getattr(c, k) for k in Candidate.FIXED}, extra=c.extra)


class CandidateDetailOut(BaseModel):
    candidate: CandidateOut
    evidence: List[Dict[str, Any]]


class BridgeOut(BaseModel):
    gene_id: str
    label: str
    symbol: str = ""
    n_articles: int
    source: str
    species: str = ""
    specificity: Optional[float] = None
    used: bool
    has_literature: bool
    n_literature: int
