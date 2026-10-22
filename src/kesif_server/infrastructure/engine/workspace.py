"""Çalışma dizini ↔ veritabanı eşitlemesi (WorkspaceSync port'u).

Motor dosya tabanlı koşar; kalıcı depo veritabanıdır. Düzen (çalışma dizinine göreli):

    data/drugs/*, data/genes/*, data/mesh/*      → scope "shared"   (ilaç/gen/hastalık sözlükleri, tüm hastalıklar ortak)
    data/benchmark/*                             → scope "shared"   (yöntem karnesi ve birleştirme kararı)
    data/trials/*                                → scope "shared"   (klinik çalışma atlası; ham indirme data/trials_raw yerelde kalır)
    data/<hastalık>/*, results/<hastalık>/*     → scope "<hastalık>"

`prepare` yalnızca sha256'sı farklı/eksik dosyaları indirir, `save` yalnızca değişenleri yükler; veritabanında olup yerelde
silinmiş dosya `save` ile silinir. Çalışma dizini her an silinebilir; bir sonraki `prepare` yeniden kurar.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from ...domain.entities import StoredFile
from ...domain.ports import UnitOfWorkFactory

log = logging.getLogger(__name__)

SHARED = "shared"
SHARED_DIRS = ("data/drugs", "data/genes", "data/mesh", "data/benchmark", "data/trials")
SKIP_NAMES = {"job.log", ".DS_Store"}  # günlük veritabanında ayrıca tutulur (analysis_runs.log)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Workspace:
    def __init__(self, uow_factory: UnitOfWorkFactory, work_dir: Path):
        self._uow = uow_factory
        self.root = Path(work_dir)
        self.data_dir = self.root / "data"
        self.results_dir = self.root / "results"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)

    # ---- kapsam → yerel dizinler ----

    def _dirs(self, scope: str) -> List[Path]:
        if scope == SHARED:
            return [self.root / d for d in SHARED_DIRS]
        return [self.data_dir / scope, self.results_dir / scope]

    def _local_files(self, scope: str) -> Dict[str, Path]:
        out: Dict[str, Path] = {}
        for d in self._dirs(scope):
            if not d.exists():
                continue
            for p in d.rglob("*"):
                if p.is_file() and p.name not in SKIP_NAMES:
                    out[p.relative_to(self.root).as_posix()] = p
        return out

    # ---- port ----

    def prepare(self, disease_key: Optional[str] = None) -> Dict[str, int]:
        """Veritabanındaki dosyaları çalışma dizinine indirir (eksik ya da farklı olanları)."""
        scopes = [SHARED] + ([disease_key] if disease_key else [])
        downloaded = skipped = removed = 0
        for scope in scopes:
            local = self._local_files(scope)
            with self._uow() as uow:
                remote = {m.path: m for m in uow.files.list_meta(scope)}
                for rel, meta in remote.items():
                    target = self.root / rel
                    if rel in local and _sha(target.read_bytes()) == meta.sha256:
                        skipped += 1
                        continue
                    stored = uow.files.get(rel)
                    if stored is None or stored.content is None:
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(stored.content)
                    downloaded += 1
            # veritabanı tek gerçek kaynak: orada olmayan yerel dosya eski bir çalıştırmanın artığıdır
            for rel, path in local.items():
                if rel not in remote:
                    path.unlink(missing_ok=True)
                    removed += 1
        log.info("Çalışma dizini hazır: %d indirildi, %d güncel, %d artık silindi (%s)", downloaded, skipped, removed, ", ".join(scopes))
        return {"downloaded": downloaded, "up_to_date": skipped, "removed": removed}

    def save(self, disease_key: Optional[str] = None) -> Dict[str, int]:
        """Çalışma dizinindeki değişiklikleri veritabanına yazar; yerelde silinenleri veritabanından da siler."""
        scopes = [SHARED] + ([disease_key] if disease_key else [])
        uploaded = unchanged = deleted = 0
        for scope in scopes:
            local = self._local_files(scope)
            with self._uow() as uow:
                remote = {m.path: m for m in uow.files.list_meta(scope)}
                for rel, path in local.items():
                    data = path.read_bytes()
                    digest = _sha(data)
                    if rel in remote and remote[rel].sha256 == digest:
                        unchanged += 1
                        continue
                    uow.files.put(StoredFile(path=rel, scope=scope, sha256=digest, size=len(data), content=data))
                    uploaded += 1
                for rel in set(remote) - set(local):
                    uow.files.delete(rel)
                    deleted += 1
                uow.commit()
        log.info("Çalışma dizini kaydedildi: %d yüklendi, %d değişmedi, %d silindi (%s)", uploaded, unchanged, deleted, ", ".join(scopes))
        return {"uploaded": uploaded, "unchanged": unchanged, "deleted": deleted}

    def discard(self, disease_key: str) -> int:
        """Hastalığın dosyalarını hem veritabanından hem çalışma dizininden kaldırır (kalıcı silme)."""
        with self._uow() as uow:
            n = uow.files.delete_scope(disease_key)
            uow.commit()
        for d in self._dirs(disease_key):
            if d.exists():
                shutil.rmtree(d)
        return n

    def import_tree(self, legacy_root: Path) -> None:
        """Eski kurulumun data/ ve results/ klasörlerini çalışma dizinine kopyalar (ardından `save` çağrılır)."""
        for name in ("data", "results"):
            src = Path(legacy_root) / name
            if src.exists() and src.resolve() != (self.root / name).resolve():
                shutil.copytree(src, self.root / name, dirs_exist_ok=True)
