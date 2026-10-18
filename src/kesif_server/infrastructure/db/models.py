"""SQLAlchemy 2.0 ORM modelleri. Alan varlıklarından ayrıdır; eşleme `repositories.py` içindedir.

Şema değişiklikleri yalnızca Alembic migration ile yapılır (alembic/versions).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="viewer")
    password_hash: Mapped[Optional[str]] = mapped_column(String(300))
    external_subject: Mapped[Optional[str]] = mapped_column(String(300), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class DiseaseModel(Base):
    __tablename__ = "diseases"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    label: Mapped[str] = mapped_column(String(300), nullable=False)
    name_en: Mapped[str] = mapped_column(String(300), nullable=False)
    mesh_id: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    pubmed_query: Mapped[str] = mapped_column(Text, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_removed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    params: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)  # sıralama parametreleri (yoksa motor varsayılanı)
    created_by: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AnalysisRunModel(Base):
    __tablename__ = "analysis_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    disease_key: Mapped[str] = mapped_column(String(120), ForeignKey("diseases.key", ondelete="CASCADE"), nullable=False)
    steps: Mapped[List[str]] = mapped_column(JSON, nullable=False)
    params: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    options: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    error: Mapped[Optional[str]] = mapped_column(Text)
    summary: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)
    progress: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    log: Mapped[Optional[str]] = mapped_column(Text)  # iş günlüğü (son 2000 satır)
    created_by: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_runs_disease_status", "disease_key", "status"), Index("ix_runs_created", "created_at"))


class CandidateModel(Base):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    disease_key: Mapped[str] = mapped_column(String(120), ForeignKey("diseases.key", ondelete="CASCADE"), nullable=False)
    run_id: Mapped[Optional[str]] = mapped_column(String(32))
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    chemical_id: Mapped[str] = mapped_column(String(64), nullable=False)
    chemical: Mapped[str] = mapped_column(String(300), nullable=False)
    chembl_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    score: Mapped[float] = mapped_column(Float, nullable=False)
    lit_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    mech_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    penalty: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    direction: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    mechanism: Mapped[str] = mapped_column(Text, nullable=False, default="")
    max_phase: Mapped[Optional[float]] = mapped_column(Float)
    n_direct_a: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_bridges: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    top_bridges: Mapped[str] = mapped_column(Text, nullable=False, default="")
    aliases: Mapped[str] = mapped_column(Text, nullable=False, default="")
    kind: Mapped[str] = mapped_column(String(30), nullable=False, default="ilaç")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="yeni")
    harm_signal: Mapped[str] = mapped_column(String(30), nullable=False, default="yok")
    durum: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    clinical_trials: Mapped[Optional[int]] = mapped_column(Integer)
    pubmed_direct: Mapped[Optional[int]] = mapped_column(Integer)
    extra: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    search_text: Mapped[str] = mapped_column(Text, nullable=False, default="")  # küçük harf: ad + eş adlar + mekanizma + köprüler

    __table_args__ = (
        UniqueConstraint("disease_key", "chemical_id", name="uq_candidate_disease_chemical"),
        Index("ix_candidates_disease_rank", "disease_key", "rank"),
        Index("ix_candidates_disease_score", "disease_key", "score"),
        Index("ix_candidates_disease_kind_status", "disease_key", "kind", "status"),
    )


class AnalysisArtifactModel(Base):
    """Analizin aday listesi dışındaki çıktıları: kanıt zinciri, köprü genler, bilinen tedaviler, doğrulama, parametre
    ayarı, keşif özeti, rapor (HTML) ve motor durum anlık görüntüsü. Hastalık başına tür başına tek kayıt."""

    __tablename__ = "analysis_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    disease_key: Mapped[str] = mapped_column(String(120), ForeignKey("diseases.key", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    run_id: Mapped[Optional[str]] = mapped_column(String(32))
    payload: Mapped[Optional[Any]] = mapped_column(JSON)
    text: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (UniqueConstraint("disease_key", "kind", name="uq_artifact_disease_kind"),)


class EngineFileModel(Base):
    """Motorun çalışma dosyaları (indirilen makaleler, gen/ilaç/hastalık sözlükleri, derlem indeksi…).

    Motor bir çalışma dizininde dosya tabanlı koşar; `Workspace` çalıştırma öncesi bu tablodan dizini doldurur,
    sonrası değişen dosyaları (sha256) geri yazar. Böylece kod deposunda ve sunucu diskinde kalıcı veri yoktur.
    scope: 'shared' (ilaç/gen/hastalık sözlükleri) ya da hastalık anahtarı.
    """

    __tablename__ = "engine_files"

    path: Mapped[str] = mapped_column(String(400), primary_key=True)  # çalışma dizinine göreli, '/' ayraçlı
    scope: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
