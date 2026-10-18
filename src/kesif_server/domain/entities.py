"""Alan varlıkları ve değer nesneleri (saf Python dataclass'ları)."""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


def new_id() -> str:
    return uuid.uuid4().hex


class Role(str, enum.Enum):
    ADMIN = "admin"      # kullanıcı yönetimi, hastalık silme, sistem ayarları
    ANALYST = "analyst"  # hastalık ekleme, analiz başlatma, parametre değiştirme
    VIEWER = "viewer"    # yalnızca okuma

    @property
    def level(self) -> int:
        return {"viewer": 1, "analyst": 2, "admin": 3}[self.value]

    def at_least(self, other: Role) -> bool:
        return self.level >= other.level


class RunStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_active(self) -> bool:
        return self in (RunStatus.QUEUED, RunStatus.RUNNING)

    @property
    def is_terminal(self) -> bool:
        return not self.is_active


# Motorun ortak dizinleriyle (data/drugs, data/benchmark...) çakıştığı için hastalık anahtarı olamaz
RESERVED_DISEASE_KEYS = frozenset({"drugs", "genes", "mesh", "benchmark"})


class AnalysisStep(str, enum.Enum):
    """Analiz adımları; sıralama motorun çalıştırma sırasıdır."""

    FETCH = "fetch"        # makaleleri indir ve işaretle
    DRUGS = "drugs"        # ilaç sözlüğünü kur
    LABELS = "labels"      # doğrulama etiketleri (klinik çalışma kayıtları)
    EVIDENCE = "evidence"  # literatürden bağımsız kanıt (genetik ilişki, protein ağı)
    TUNE = "tune"          # parametre ayarı
    RANK = "rank"          # aday sıralama
    BACKTEST = "backtest"  # geriye dönük doğrulama
    NOVELTY = "novelty"    # yenilik kontrolü
    REPORT = "report"      # rapor

    @classmethod
    def ordered(cls, steps: List[str]) -> List[AnalysisStep]:
        wanted = {cls(s) for s in steps}
        return [s for s in cls if s in wanted]


DEFAULT_STEPS = [AnalysisStep.FETCH, AnalysisStep.DRUGS, AnalysisStep.LABELS, AnalysisStep.EVIDENCE, AnalysisStep.RANK,
                 AnalysisStep.BACKTEST, AnalysisStep.NOVELTY, AnalysisStep.REPORT]


class CandidateStatus(str, enum.Enum):
    NEW = "yeni"
    MENTIONED = "literatürde anılmış"
    STUDIED = "tedavi olarak çalışılmış"


class DrugKind(str, enum.Enum):
    DRUG = "ilaç"
    ENDOGENOUS = "endojen"
    NUTRIENT = "besin/takviye"
    DIAGNOSTIC = "tanı yardımcısı"


@dataclass
class User:
    email: str
    display_name: str
    role: Role = Role.VIEWER
    id: str = field(default_factory=new_id)
    password_hash: Optional[str] = None
    external_subject: Optional[str] = None  # OIDC 'sub'
    is_active: bool = True
    created_at: Optional[datetime] = None
    last_login_at: Optional[datetime] = None

    def can(self, role: Role) -> bool:
        return self.is_active and self.role.at_least(role)


@dataclass
class Disease:
    key: str
    label: str
    name_en: str
    pubmed_query: str
    mesh_id: str = ""
    params: Optional[Dict[str, Any]] = None  # sıralama parametreleri; None = motor varsayılanı
    is_default: bool = False   # paketle gelen katalogdan
    is_removed: bool = False
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_engine_config(self) -> Dict[str, str]:
        return {"label": self.label, "name_en": self.name_en, "mesh_id": self.mesh_id, "pubmed_query": self.pubmed_query}


@dataclass
class RunOptions:
    cutoff: int = 2018
    cutoff_a: int = 2016
    cutoff_b: int = 2019
    top: int = 100
    start_year: int = 1990
    max_a: Optional[int] = 25000
    per_gene: int = 300
    force: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RunOptions:
        return cls(**{k: v for k, v in (data or {}).items() if k in cls.__dataclass_fields__})


@dataclass
class RunProgress:
    step: Optional[str] = None
    message: str = ""
    done: Optional[int] = None
    total: Optional[int] = None
    completed_steps: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"step": self.step, "message": self.message, "done": self.done, "total": self.total, "completed_steps": list(self.completed_steps)}

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> RunProgress:
        data = data or {}
        return cls(step=data.get("step"), message=data.get("message", ""), done=data.get("done"), total=data.get("total"),
                   completed_steps=list(data.get("completed_steps") or []))


@dataclass
class AnalysisRun:
    disease_key: str
    steps: List[AnalysisStep]
    params: Dict[str, Any]
    options: RunOptions
    id: str = field(default_factory=new_id)
    status: RunStatus = RunStatus.QUEUED
    error: Optional[str] = None
    summary: Optional[Dict[str, Any]] = None
    progress: RunProgress = field(default_factory=RunProgress)
    attempts: int = 0
    queue_position: int = 0
    log: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    def mark_running(self, at: datetime) -> None:
        self.status = RunStatus.RUNNING
        self.started_at = self.started_at or at
        self.attempts += 1
        self.queue_position = 0

    def mark_done(self, at: datetime, summary: Dict[str, Any]) -> None:
        self.status = RunStatus.DONE
        self.summary = summary
        self.error = None
        self.finished_at = at
        self.progress = RunProgress(step=None, message="Tamamlandı", completed_steps=[s.value for s in self.steps])

    def mark_failed(self, at: datetime, error: str) -> None:
        self.status = RunStatus.FAILED
        self.error = error
        self.finished_at = at

    def mark_cancelled(self, at: datetime) -> None:
        self.status = RunStatus.CANCELLED
        self.finished_at = at


@dataclass
class Candidate:
    """Sıralanmış tek bir ilaç adayı. Sabit sütunlar sorgulanabilir; motorun diğer sütunları `extra` içinde taşınır."""

    disease_key: str
    rank: int
    chemical_id: str
    chemical: str
    score: float
    chembl_id: str = ""
    lit_score: float = 0.0
    mech_score: float = 0.0
    penalty: float = 1.0
    direction: str = ""
    mechanism: str = ""
    max_phase: Optional[float] = None
    n_direct_a: int = 0
    n_bridges: int = 0
    top_bridges: str = ""
    aliases: str = ""
    kind: str = DrugKind.DRUG.value
    status: str = CandidateStatus.NEW.value
    harm_signal: str = "yok"
    durum: str = ""
    clinical_trials: Optional[int] = None
    pubmed_direct: Optional[int] = None
    run_id: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    FIXED = ("rank", "chemical_id", "chemical", "score", "chembl_id", "lit_score", "mech_score", "penalty", "direction",
             "mechanism", "max_phase", "n_direct_a", "n_bridges", "top_bridges", "aliases", "kind", "status", "harm_signal",
             "durum", "clinical_trials", "pubmed_direct")

    @classmethod
    def from_row(cls, disease_key: str, row: Dict[str, Any], run_id: Optional[str] = None) -> Candidate:
        fixed: Dict[str, Any] = {k: row.get(k) for k in cls.FIXED if k in row}
        extra = {k: v for k, v in row.items() if k not in cls.FIXED}
        defaults = {"chembl_id": "", "lit_score": 0.0, "mech_score": 0.0, "penalty": 1.0, "direction": "", "mechanism": "",
                    "n_direct_a": 0, "n_bridges": 0, "top_bridges": "", "aliases": "", "kind": DrugKind.DRUG.value,
                    "status": CandidateStatus.NEW.value, "harm_signal": "yok", "durum": ""}
        for k, v in defaults.items():
            if fixed.get(k) is None:
                fixed[k] = v
        return cls(disease_key=disease_key, run_id=run_id, extra=extra, **fixed)

    def to_row(self) -> Dict[str, Any]:
        row = {k: getattr(self, k) for k in self.FIXED}
        row.update(self.extra)
        return row


@dataclass
class CandidateFilter:
    """Aday sorgusu süzgeçleri (API'den gelen ham değerler burada tipli hale gelir)."""

    kinds: Optional[List[str]] = None
    show_endogenous: bool = False
    statuses: Optional[List[str]] = None
    directions: Optional[List[str]] = None
    harm_signals: Optional[List[str]] = None
    durum: Optional[List[str]] = None
    min_phase: Optional[float] = None
    min_score: Optional[float] = None
    exclude_known: bool = False
    text: Optional[str] = None
    sort: str = "score"
    descending: Optional[bool] = None  # None: sütunun doğal yönü (puanlar azalan, ad/sıra artan)

    SORTABLE = ("score", "lit_score", "mech_score", "n_direct_a", "max_phase", "n_bridges", "rank", "chemical")


@dataclass
class Page:
    offset: int = 0
    limit: int = 100

    def __post_init__(self):
        self.offset = max(0, int(self.offset))
        self.limit = max(1, min(int(self.limit), 1000))


@dataclass
class TokenClaims:
    subject: str
    email: Optional[str]
    role: Optional[Role]
    token_type: str = "access"
    expires_at: Optional[datetime] = None


class ArtifactKind(str, enum.Enum):
    """Aday listesi dışındaki analiz çıktıları (veritabanında, hastalık başına tür başına tek kayıt)."""

    STATUS = "status"        # motorun adım/derlem durum anlık görüntüsü
    EVIDENCE = "evidence"    # chemical_id -> kanıt zinciri
    KNOWN = "known"          # bilinen tedaviler
    BRIDGES = "bridges"      # köprü genler
    BACKTEST = "backtest"
    TUNING = "tuning"
    DISCOVERY = "discovery"
    REPORT = "report"        # HTML (text)


@dataclass
class Artifact:
    disease_key: str
    kind: ArtifactKind
    payload: Any = None
    text: Optional[str] = None
    run_id: Optional[str] = None
    created_at: Optional[datetime] = None


@dataclass
class StoredFile:
    """Motor çalışma dosyası (veritabanındaki kopya). `content` yalnızca okunduğunda doludur."""

    path: str
    scope: str
    sha256: str
    size: int
    content: Optional[bytes] = None
    updated_at: Optional[datetime] = None
