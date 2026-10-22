"""Bir veritabanının içeriğini başka bir veritabanına eksiksiz kopyalar (ör. SQLite → PostgreSQL).

Şema hedefte önceden Alembic ile kurulmuş olmalıdır (`kesif migrate`); bu modül yalnızca satırları taşır ve
taşımanın doğruluğunu ölçer:

    - tablolar yabancı anahtar sırasıyla (Base.metadata.sorted_tables) kopyalanır;
    - büyük ikili içerikli tablolar (engine_files) bellek sınırı gözetilerek bayt bütçesiyle partilenir;
    - saat dilimi bilgisi olmayan zaman değerleri UTC kabul edilir (SQLite saat dilimini saklamaz);
    - PostgreSQL'de otomatik artan kimlik dizileri kopyadan sonra en büyük kimliğe ayarlanır;
    - doğrulama: her tabloda satır sayısı eşit olmalı; engine_files'ta her satırın içeriğinin SHA-256 özeti kayıtlı
      özetle aynı olmalı ve toplam bayt eşit olmalı.

Hedefte veri varsa kopyalama yapılmaz (yanlışlıkla üzerine yazmayı önlemek için).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import DateTime, Integer, LargeBinary, func, select, text
from sqlalchemy.engine import Engine

from .models import Base

ROW_BATCH = 2000
BYTE_BUDGET = 64 * 1024 * 1024  # ikili içerikli tablolarda bir partide en fazla ~64 MB


def _fix_row(row: Dict[str, Any], tz_cols: List[str]) -> Dict[str, Any]:
    for c in tz_cols:
        v = row.get(c)
        if isinstance(v, datetime) and v.tzinfo is None:
            row[c] = v.replace(tzinfo=timezone.utc)
    return row


def _counts(engine: Engine) -> Dict[str, int]:
    with engine.connect() as conn:
        return {t.name: int(conn.execute(select(func.count()).select_from(t)).scalar() or 0) for t in Base.metadata.sorted_tables}


def copy_database(src: Engine, dst: Engine, progress: Optional[Callable[[str], None]] = None) -> dict:
    say = progress or (lambda _m: None)
    tables = Base.metadata.sorted_tables
    existing = {k: v for k, v in _counts(dst).items() if v}
    if existing:
        raise RuntimeError(f"Hedef veritabanında veri var, kopyalama yapılmadı: {existing}")
    copied: Dict[str, int] = {}
    for t in tables:
        tz_cols = [c.name for c in t.columns if isinstance(c.type, DateTime)]
        has_blob = any(isinstance(c.type, LargeBinary) for c in t.columns)
        n = 0
        batch: List[Dict[str, Any]] = []
        size = 0
        with src.connect() as sconn, dst.begin() as dconn:
            result = sconn.execution_options(stream_results=True, yield_per=200 if has_blob else ROW_BATCH).execute(select(t))
            for r in result.mappings():
                row = _fix_row(dict(r), tz_cols)
                batch.append(row)
                if has_blob:
                    size += sum(len(v) for v in row.values() if isinstance(v, (bytes, bytearray, memoryview)))
                if len(batch) >= ROW_BATCH or (has_blob and size >= BYTE_BUDGET):
                    dconn.execute(t.insert(), batch)
                    n += len(batch)
                    batch, size = [], 0
                    say(f"  {t.name}: {n} satır")
            if batch:
                dconn.execute(t.insert(), batch)
                n += len(batch)
        copied[t.name] = n
        say(f"{t.name}: {n} satır kopyalandı")
    if dst.dialect.name == "postgresql":
        with dst.begin() as conn:
            for t in tables:
                for c in t.primary_key.columns:
                    if isinstance(c.type, Integer) and c.autoincrement in (True, "auto"):
                        conn.execute(text(
                            f"SELECT setval(pg_get_serial_sequence('{t.name}', '{c.name}'), "
                            f"COALESCE((SELECT MAX({c.name}) FROM {t.name}), 1), "
                            f"(SELECT MAX({c.name}) FROM {t.name}) IS NOT NULL)"))
    return {"copied": copied, "verification": verify(src, dst, progress)}


def verify(src: Engine, dst: Engine, progress: Optional[Callable[[str], None]] = None) -> dict:
    """Satır sayıları ve ikili içerik bütünlüğü. {"ok": bool, "tables": {...}, "engine_files": {...}}"""
    say = progress or (lambda _m: None)
    a, b = _counts(src), _counts(dst)
    tables = {k: {"kaynak": a[k], "hedef": b.get(k, 0), "ok": a[k] == b.get(k, 0)} for k in a}
    files: Dict[str, Any] = {"ok": True}
    ef = Base.metadata.tables.get("engine_files")
    if ef is not None:
        bad, total_src, total_dst, n = [], 0, 0, 0
        with src.connect() as conn:
            total_src = int(conn.execute(select(func.coalesce(func.sum(ef.c.size), 0))).scalar() or 0)
        with dst.connect() as conn:
            rows = conn.execution_options(stream_results=True, yield_per=100).execute(select(ef.c.path, ef.c.sha256, ef.c.content))
            for path, sha, content in rows:
                n += 1
                data = bytes(content)
                total_dst += len(data)
                if hashlib.sha256(data).hexdigest() != sha:
                    bad.append(path)
                if n % 500 == 0:
                    say(f"  içerik doğrulama: {n} dosya")
        files = {"n_files": n, "bytes_source": total_src, "bytes_target": total_dst, "sha256_mismatch": bad[:20],
                 "ok": not bad and total_src == total_dst}
    return {"ok": all(v["ok"] for v in tables.values()) and files["ok"], "tables": tables, "engine_files": files}
