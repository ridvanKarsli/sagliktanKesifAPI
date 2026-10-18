"""Depo uygulamaları: ORM modeli ↔ alan varlığı eşlemesi ve sorgular."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, List, Optional, Tuple

from sqlalchemy import asc, delete, desc, func, or_, select
from sqlalchemy.orm import Session

from ...domain.entities import (
    AnalysisRun,
    AnalysisStep,
    Artifact,
    ArtifactKind,
    Candidate,
    CandidateFilter,
    Disease,
    Page,
    Role,
    RunOptions,
    RunProgress,
    RunStatus,
    StoredFile,
    User,
)
from .models import AnalysisArtifactModel, AnalysisRunModel, CandidateModel, DiseaseModel, EngineFileModel, UserModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------- kullanıcılar ----------------

class SqlUserRepository:
    def __init__(self, session: Session):
        self._s = session

    @staticmethod
    def _to_entity(m: UserModel) -> User:
        return User(id=m.id, email=m.email, display_name=m.display_name, role=Role(m.role), password_hash=m.password_hash,
                    external_subject=m.external_subject, is_active=m.is_active, created_at=m.created_at, last_login_at=m.last_login_at)

    @staticmethod
    def _apply(m: UserModel, u: User) -> None:
        m.email, m.display_name, m.role, m.password_hash = u.email, u.display_name, u.role.value, u.password_hash
        m.external_subject, m.is_active, m.last_login_at = u.external_subject, u.is_active, u.last_login_at

    def get(self, user_id: str) -> Optional[User]:
        m = self._s.get(UserModel, user_id)
        return self._to_entity(m) if m else None

    def get_by_email(self, email: str) -> Optional[User]:
        m = self._s.scalar(select(UserModel).where(UserModel.email == email.lower()))
        return self._to_entity(m) if m else None

    def get_by_subject(self, subject: str) -> Optional[User]:
        m = self._s.scalar(select(UserModel).where(UserModel.external_subject == subject))
        return self._to_entity(m) if m else None

    def add(self, user: User) -> User:
        user.created_at = user.created_at or _now()
        m = UserModel(id=user.id, created_at=user.created_at)
        self._apply(m, user)
        self._s.add(m)
        self._s.flush()
        return user

    def update(self, user: User) -> User:
        m = self._s.get(UserModel, user.id)
        if m is None:
            raise KeyError(user.id)
        self._apply(m, user)
        self._s.flush()
        return user

    def list(self) -> List[User]:
        return [self._to_entity(m) for m in self._s.scalars(select(UserModel).order_by(UserModel.created_at))]

    def count(self) -> int:
        return int(self._s.scalar(select(func.count()).select_from(UserModel)) or 0)


# ---------------- hastalıklar ----------------

class SqlDiseaseRepository:
    def __init__(self, session: Session):
        self._s = session

    @staticmethod
    def _to_entity(m: DiseaseModel) -> Disease:
        return Disease(key=m.key, label=m.label, name_en=m.name_en, pubmed_query=m.pubmed_query, mesh_id=m.mesh_id, params=m.params,
                       is_default=m.is_default, is_removed=m.is_removed, created_by=m.created_by, created_at=m.created_at, updated_at=m.updated_at)

    def get(self, key: str) -> Optional[Disease]:
        m = self._s.get(DiseaseModel, key)
        return self._to_entity(m) if m else None

    def list(self, include_removed: bool = False) -> List[Disease]:
        stmt = select(DiseaseModel).order_by(DiseaseModel.label)
        if not include_removed:
            stmt = stmt.where(DiseaseModel.is_removed.is_(False))
        return [self._to_entity(m) for m in self._s.scalars(stmt)]

    def add(self, d: Disease) -> Disease:
        d.created_at = d.created_at or _now()
        d.updated_at = d.created_at
        self._s.add(DiseaseModel(key=d.key, label=d.label, name_en=d.name_en, mesh_id=d.mesh_id or "", pubmed_query=d.pubmed_query,
                                 params=d.params, is_default=d.is_default, is_removed=d.is_removed, created_by=d.created_by,
                                 created_at=d.created_at, updated_at=d.updated_at))
        self._s.flush()
        return d

    def update(self, d: Disease) -> Disease:
        m = self._s.get(DiseaseModel, d.key)
        if m is None:
            raise KeyError(d.key)
        m.label, m.name_en, m.mesh_id, m.pubmed_query = d.label, d.name_en, d.mesh_id or "", d.pubmed_query
        m.is_removed, m.params, m.updated_at = d.is_removed, d.params, _now()
        d.updated_at = m.updated_at
        self._s.flush()
        return d


# ---------------- analiz işleri ----------------

class SqlAnalysisRunRepository:
    def __init__(self, session: Session):
        self._s = session

    @staticmethod
    def _to_entity(m: AnalysisRunModel, position: int = 0) -> AnalysisRun:
        return AnalysisRun(id=m.id, disease_key=m.disease_key, steps=[AnalysisStep(s) for s in m.steps], params=dict(m.params),
                           options=RunOptions.from_dict(m.options), status=RunStatus(m.status), error=m.error, summary=m.summary,
                           progress=RunProgress.from_dict(m.progress), attempts=m.attempts, queue_position=position, log=m.log,
                           created_by=m.created_by, created_at=m.created_at, started_at=m.started_at, finished_at=m.finished_at)

    def _position(self, run: AnalysisRun) -> int:
        if run.status != RunStatus.QUEUED:
            return 0
        earlier = self._s.scalar(select(func.count()).select_from(AnalysisRunModel).where(
            AnalysisRunModel.status == RunStatus.QUEUED.value, AnalysisRunModel.created_at < run.created_at))
        return int(earlier or 0) + 1

    def _with_position(self, m: AnalysisRunModel) -> AnalysisRun:
        run = self._to_entity(m)
        run.queue_position = self._position(run)
        return run

    def get(self, run_id: str) -> Optional[AnalysisRun]:
        m = self._s.get(AnalysisRunModel, run_id)
        return self._with_position(m) if m else None

    def add(self, run: AnalysisRun) -> AnalysisRun:
        run.created_at = run.created_at or _now()
        self._s.add(AnalysisRunModel(id=run.id, disease_key=run.disease_key, steps=[s.value for s in run.steps], params=run.params,
                                     options=run.options.to_dict(), status=run.status.value, error=run.error, summary=run.summary,
                                     progress=run.progress.to_dict(), attempts=run.attempts, created_by=run.created_by,
                                     created_at=run.created_at, started_at=run.started_at, finished_at=run.finished_at))
        self._s.flush()
        return run

    def update(self, run: AnalysisRun) -> AnalysisRun:
        m = self._s.get(AnalysisRunModel, run.id)
        if m is None:
            raise KeyError(run.id)
        m.status, m.error, m.summary, m.progress = run.status.value, run.error, run.summary, run.progress.to_dict()
        m.attempts, m.started_at, m.finished_at, m.log = run.attempts, run.started_at, run.finished_at, run.log
        self._s.flush()
        return run

    def active_for(self, disease_key: str) -> Optional[AnalysisRun]:
        m = self._s.scalar(select(AnalysisRunModel).where(AnalysisRunModel.disease_key == disease_key,
                                                          AnalysisRunModel.status.in_([RunStatus.QUEUED.value, RunStatus.RUNNING.value]))
                           .order_by(AnalysisRunModel.created_at))
        return self._with_position(m) if m else None

    def latest_for(self, disease_key: str) -> Optional[AnalysisRun]:
        m = self._s.scalar(select(AnalysisRunModel).where(AnalysisRunModel.disease_key == disease_key)
                           .order_by(desc(AnalysisRunModel.created_at)).limit(1))
        return self._with_position(m) if m else None

    def list(self, disease_key: Optional[str] = None, limit: int = 50) -> List[AnalysisRun]:
        stmt = select(AnalysisRunModel).order_by(desc(AnalysisRunModel.created_at)).limit(limit)
        if disease_key:
            stmt = stmt.where(AnalysisRunModel.disease_key == disease_key)
        return [self._with_position(m) for m in self._s.scalars(stmt)]

    def queued(self) -> List[AnalysisRun]:
        stmt = select(AnalysisRunModel).where(AnalysisRunModel.status == RunStatus.QUEUED.value).order_by(AnalysisRunModel.created_at)
        out = []
        for i, m in enumerate(self._s.scalars(stmt), 1):
            out.append(self._to_entity(m, i))
        return out


# ---------------- adaylar ----------------

class SqlCandidateRepository:
    def __init__(self, session: Session):
        self._s = session

    @staticmethod
    def _to_entity(m: CandidateModel) -> Candidate:
        return Candidate(disease_key=m.disease_key, rank=m.rank, chemical_id=m.chemical_id, chemical=m.chemical, score=m.score,
                         chembl_id=m.chembl_id, lit_score=m.lit_score, mech_score=m.mech_score, penalty=m.penalty, direction=m.direction,
                         mechanism=m.mechanism, max_phase=m.max_phase, n_direct_a=m.n_direct_a, n_bridges=m.n_bridges,
                         top_bridges=m.top_bridges, aliases=m.aliases, kind=m.kind, status=m.status, harm_signal=m.harm_signal,
                         durum=m.durum, clinical_trials=m.clinical_trials, pubmed_direct=m.pubmed_direct, run_id=m.run_id, extra=dict(m.extra or {}))

    @staticmethod
    def _to_model(c: Candidate) -> CandidateModel:
        text = " ".join(str(x) for x in (c.chemical, c.aliases, c.mechanism, c.top_bridges, c.chembl_id) if x).lower()
        return CandidateModel(disease_key=c.disease_key, run_id=c.run_id, rank=int(c.rank), chemical_id=str(c.chemical_id), chemical=str(c.chemical),
                              chembl_id=c.chembl_id or "", score=float(c.score or 0), lit_score=float(c.lit_score or 0),
                              mech_score=float(c.mech_score or 0), penalty=float(c.penalty if c.penalty is not None else 1.0),
                              direction=c.direction or "", mechanism=c.mechanism or "", max_phase=c.max_phase,
                              n_direct_a=int(c.n_direct_a or 0), n_bridges=int(c.n_bridges or 0), top_bridges=c.top_bridges or "",
                              aliases=c.aliases or "", kind=c.kind or "ilaç", status=c.status or "yeni", harm_signal=c.harm_signal or "yok",
                              durum=c.durum or "", clinical_trials=c.clinical_trials, pubmed_direct=c.pubmed_direct,
                              extra=_json_safe(c.extra), search_text=text)

    def replace_for_disease(self, disease_key: str, run_id: Optional[str], candidates: Iterable[Candidate]) -> int:
        self._s.execute(delete(CandidateModel).where(CandidateModel.disease_key == disease_key))
        n = 0
        batch: List[CandidateModel] = []
        for c in candidates:
            c.disease_key, c.run_id = disease_key, run_id
            batch.append(self._to_model(c))
            n += 1
            if len(batch) >= 500:
                self._s.add_all(batch)
                self._s.flush()
                batch = []
        if batch:
            self._s.add_all(batch)
        self._s.flush()
        return n

    def delete_for_disease(self, disease_key: str) -> int:
        res = self._s.execute(delete(CandidateModel).where(CandidateModel.disease_key == disease_key))
        return int(res.rowcount or 0)

    def count_for(self, disease_key: str) -> int:
        return int(self._s.scalar(select(func.count()).select_from(CandidateModel).where(CandidateModel.disease_key == disease_key)) or 0)

    def get(self, disease_key: str, chemical_id: str) -> Optional[Candidate]:
        m = self._s.scalar(select(CandidateModel).where(CandidateModel.disease_key == disease_key, CandidateModel.chemical_id == chemical_id))
        return self._to_entity(m) if m else None

    def query(self, disease_key: str, flt: CandidateFilter, page: Page) -> Tuple[int, List[Candidate]]:
        conds = [CandidateModel.disease_key == disease_key]
        if flt.kinds:
            conds.append(CandidateModel.kind.in_(flt.kinds))
        elif not flt.show_endogenous:
            conds.append(CandidateModel.kind != "endojen")
        if flt.statuses:
            conds.append(CandidateModel.status.in_(flt.statuses))
        if flt.directions:
            conds.append(CandidateModel.direction.in_(flt.directions))
        if flt.harm_signals:
            conds.append(CandidateModel.harm_signal.in_(flt.harm_signals))
        if flt.durum:
            conds.append(CandidateModel.durum.in_(flt.durum))
        if flt.min_phase is not None:
            conds.append(func.coalesce(CandidateModel.max_phase, 0) >= flt.min_phase)
        if flt.min_score is not None:
            conds.append(CandidateModel.score >= flt.min_score)
        if flt.exclude_known:
            conds.append(or_(CandidateModel.n_direct_a == 0, CandidateModel.n_direct_a.is_(None)))
        if flt.text:
            conds.append(CandidateModel.search_text.like(f"%{flt.text.lower().strip()}%"))
        total = int(self._s.scalar(select(func.count()).select_from(CandidateModel).where(*conds)) or 0)
        sort = flt.sort if flt.sort in CandidateFilter.SORTABLE else "score"
        col = getattr(CandidateModel, sort)
        # doğal yön: ad ve sıra artan, puan türü sütunlar azalan; `descending` açıkça verilirse onu uygular
        natural_desc = sort not in ("chemical", "rank")
        descending = natural_desc if flt.descending is None else flt.descending
        key = col if sort in ("chemical", "rank") else func.coalesce(col, 0)
        order = [desc(key) if descending else asc(key), asc(CandidateModel.rank)]
        stmt = select(CandidateModel).where(*conds).order_by(*order).offset(page.offset).limit(page.limit)
        return total, [self._to_entity(m) for m in self._s.scalars(stmt)]


# ---------------- analiz çıktıları ----------------

class SqlArtifactRepository:
    def __init__(self, session: Session):
        self._s = session

    @staticmethod
    def _to_entity(m: AnalysisArtifactModel) -> Artifact:
        return Artifact(disease_key=m.disease_key, kind=ArtifactKind(m.kind), payload=m.payload, text=m.text, run_id=m.run_id, created_at=m.created_at)

    def put(self, a: Artifact) -> Artifact:
        m = self._s.scalar(select(AnalysisArtifactModel).where(AnalysisArtifactModel.disease_key == a.disease_key,
                                                               AnalysisArtifactModel.kind == a.kind.value))
        a.created_at = _now()
        if m is None:
            m = AnalysisArtifactModel(disease_key=a.disease_key, kind=a.kind.value)
            self._s.add(m)
        m.payload, m.text, m.run_id, m.created_at = a.payload, a.text, a.run_id, a.created_at
        self._s.flush()
        return a

    def get(self, disease_key: str, kind: ArtifactKind) -> Optional[Artifact]:
        m = self._s.scalar(select(AnalysisArtifactModel).where(AnalysisArtifactModel.disease_key == disease_key,
                                                               AnalysisArtifactModel.kind == kind.value))
        return self._to_entity(m) if m else None

    def kinds_for(self, disease_key: str) -> List[ArtifactKind]:
        rows = self._s.scalars(select(AnalysisArtifactModel.kind).where(AnalysisArtifactModel.disease_key == disease_key))
        return [ArtifactKind(k) for k in rows]

    def delete_for_disease(self, disease_key: str) -> int:
        res = self._s.execute(delete(AnalysisArtifactModel).where(AnalysisArtifactModel.disease_key == disease_key))
        return int(res.rowcount or 0)


# ---------------- motor dosyaları ----------------

class SqlFileRepository:
    def __init__(self, session: Session):
        self._s = session

    def list_meta(self, scope: str) -> List[StoredFile]:
        stmt = select(EngineFileModel.path, EngineFileModel.scope, EngineFileModel.sha256, EngineFileModel.size, EngineFileModel.updated_at).where(
            EngineFileModel.scope == scope)
        return [StoredFile(path=r.path, scope=r.scope, sha256=r.sha256, size=r.size, updated_at=r.updated_at) for r in self._s.execute(stmt)]

    def get(self, path: str) -> Optional[StoredFile]:
        m = self._s.get(EngineFileModel, path)
        return StoredFile(path=m.path, scope=m.scope, sha256=m.sha256, size=m.size, content=m.content, updated_at=m.updated_at) if m else None

    def put(self, f: StoredFile) -> None:
        m = self._s.get(EngineFileModel, f.path)
        if m is None:
            m = EngineFileModel(path=f.path)
            self._s.add(m)
        m.scope, m.sha256, m.size, m.content, m.updated_at = f.scope, f.sha256, f.size, f.content or b"", _now()
        self._s.flush()

    def delete(self, path: str) -> None:
        self._s.execute(delete(EngineFileModel).where(EngineFileModel.path == path))

    def delete_scope(self, scope: str) -> int:
        res = self._s.execute(delete(EngineFileModel).where(EngineFileModel.scope == scope))
        return int(res.rowcount or 0)


def _json_safe(d):
    out = {}
    for k, v in (d or {}).items():
        if hasattr(v, "item"):
            v = v.item()
        if isinstance(v, float) and v != v:  # NaN
            v = None
        out[k] = v
    return out
