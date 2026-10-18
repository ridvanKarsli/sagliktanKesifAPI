"""Bileşim kökü (composition root): ayarlardan somut bağımlılıkları kurar, kullanım senaryolarını bağlar.

Uygulamanın `new` ile nesne yarattığı tek yer burasıdır; diğer katmanlar yalnızca port alır. API ve worker aynı
kökü paylaşır; testler `Container(settings, overrides)` ile sahte port'lar geçirir.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

from kesif_engine import config as engine_config

from .application.use_cases import analyses, auth, candidates, catalog, diseases
from .core.clock import SystemClock
from .core.logging import configure_logging
from .core.settings import Settings, get_settings
from .domain.entities import Role, User
from .domain.ports import (
    AnalysisEngine,
    DiseaseCatalog,
    ExternalServices,
    JobQueue,
    PasswordHasher,
    ResultReader,
    TokenService,
    WorkspaceSync,
)
from .infrastructure.auth.password import Argon2PasswordHasher
from .infrastructure.auth.tokens import LocalJwtTokenService, OidcTokenService
from .infrastructure.db.session import make_engine, make_session_factory
from .infrastructure.db.uow import make_uow_factory
from .infrastructure.engine.adapter import CatalogAdapter, EngineAdapter, ExternalServicesAdapter, ResultReaderAdapter, configure_engine
from .infrastructure.engine.workspace import Workspace

log = logging.getLogger(__name__)

LOCAL_ADMIN_EMAIL = "system@kesif.local"


class Container:
    def __init__(self, settings: Settings, *, engine: Optional[AnalysisEngine] = None, reader: Optional[ResultReader] = None,
                 catalog_port: Optional[DiseaseCatalog] = None, services: Optional[ExternalServices] = None,
                 queue: Optional[JobQueue] = None, hasher: Optional[PasswordHasher] = None, tokens: Optional[TokenService] = None,
                 workspace: Optional[WorkspaceSync] = None, start_worker: bool = True):
        self.settings = settings
        self.clock = SystemClock()

        self.db_engine = make_engine(settings.database_url, settings.db_echo)
        self.session_factory = make_session_factory(self.db_engine)
        self.uow = make_uow_factory(self.session_factory)

        self.workspace_impl = Workspace(self.uow, settings.work_dir)
        self.workspace: WorkspaceSync = workspace or self.workspace_impl
        configure_engine(self.workspace_impl.data_dir, self.workspace_impl.results_dir, self._disease_provider, settings.ncbi_api_key,
                         settings.ncbi_email)
        self.engine = engine or EngineAdapter()
        self.reader = reader or ResultReaderAdapter()
        self.catalog = catalog_port or CatalogAdapter()
        self.services = services or ExternalServicesAdapter()
        self.hasher = hasher or Argon2PasswordHasher()
        self.tokens = tokens or self._make_tokens()

        # --- kullanım senaryoları ---
        self.sync_results = candidates.SyncResults(self.uow, self.reader, self.engine)
        self.execute_run = analyses.ExecuteRun(self.uow, self.engine, self.clock, self.workspace_impl.results_dir, self.sync_results,
                                               self.workspace, retry_delay_seconds=settings.job_retry_delay_seconds)
        self.queue = queue or self._make_queue(start_worker)

        self.list_diseases = diseases.ListDiseases(self.uow)
        self.get_disease = diseases.GetDisease(self.uow)
        self.add_disease = diseases.AddDisease(self.uow, self.catalog)
        self.remove_disease = diseases.RemoveDisease(self.uow, self.workspace)
        self.seed_diseases = diseases.SeedDefaultDiseases(self.uow, engine_config.load_diseases())

        self.start_analysis = analyses.StartAnalysis(self.uow, self.engine, self.queue, self.clock)
        self.cancel_analysis = analyses.CancelAnalysis(self.uow, self.clock)
        self.get_run = analyses.GetRun(self.uow)
        self.list_runs = analyses.ListRuns(self.uow)
        self.rerank = analyses.Rerank(self.uow, self.engine, self.sync_results, self.workspace)
        self.get_settings = analyses.GetSettings(self.uow, self.engine)
        self.update_settings = analyses.UpdateSettings(self.uow, self.engine)

        self.query_candidates = candidates.QueryCandidates(self.uow)
        self.get_candidate = candidates.GetCandidate(self.uow)
        self.results = candidates.ReadResults(self.uow, self.reader)

        self.catalog_state = catalog.CatalogBuildState()
        self.catalog_service = catalog.CatalogService(self.uow, self.catalog, self.catalog_state, self.workspace)
        self.external = catalog.ExternalQueries(self.uow, self.services, self.workspace)

        self.login = auth.Login(self.uow, self.hasher, self.tokens, self.clock)
        self.refresh_tokens = auth.RefreshTokens(self.uow, self.tokens)
        self.resolve_actor = auth.ResolveActor(self.uow, self.clock, auto_provision=settings.auth_mode == "oidc")
        self.create_user = auth.CreateUser(self.uow, self.hasher, self.clock)
        self.bootstrap_admin = auth.BootstrapAdmin(self.uow, self.hasher, self.clock)
        self.list_users = auth.ListUsers(self.uow)
        self.update_user = auth.UpdateUser(self.uow, self.hasher)
        self.change_own_password = auth.ChangeOwnPassword(self.uow, self.hasher)

    # ---- yardımcılar ----

    def _disease_provider(self):
        with self.uow() as uow:
            return {d.key: d.to_engine_config() for d in uow.diseases.list()}

    def _make_tokens(self) -> TokenService:
        s = self.settings
        if s.auth_mode == "oidc":
            if not s.oidc_issuer:
                raise RuntimeError("KESIF_OIDC_ISSUER gerekli (auth_mode=oidc)")
            return OidcTokenService(s.oidc_issuer, s.oidc_audience, s.oidc_jwks_url)
        return LocalJwtTokenService(s.jwt_secret, s.jwt_issuer, s.jwt_access_ttl_minutes, s.jwt_refresh_ttl_days)

    def _make_queue(self, start_worker: bool) -> JobQueue:
        if self.settings.queue_backend == "redis":
            from .infrastructure.queue.redis_queue import RedisQueue

            return RedisQueue(self.settings.redis_url, self.settings.queue_name, self.settings.job_timeout_seconds)
        from .infrastructure.queue.inprocess import InProcessQueue

        return InProcessQueue(self.execute_run, start=start_worker)

    def startup(self) -> None:
        """Açılış görevleri: varsayılan hastalıkları yaz, ilk yöneticiyi aç, yarım kalan işleri yeniden sıraya al."""
        self.seed_diseases()
        s = self.settings
        # Sistem kullanıcısı: parolası yoktur, giriş yapamaz; komut satırı işleri ve auth_mode=disabled bu kimlikle çalışır.
        with self.uow() as uow:
            if uow.users.get_by_email(LOCAL_ADMIN_EMAIL) is None:
                uow.users.add(User(email=LOCAL_ADMIN_EMAIL, display_name="Sistem", role=Role.ADMIN, created_at=self.clock.now()))
                uow.commit()
        if s.auth_mode == "local":
            created = self.bootstrap_admin(s.bootstrap_admin_email, s.bootstrap_admin_password)
            if created:
                log.info("İlk yönetici açıldı: %s", created.email)
        try:
            self.workspace.prepare()  # ortak sözlükler (ilaç/gen/hastalık) çalışma dizinine
        except Exception:
            log.exception("Ortak dosyalar çalışma dizinine alınamadı")
        self.recover_runs()

    def recover_runs(self) -> None:
        """Süreç yeniden başladığında 'running' kalan işler 'queued'a, sıradakiler kuyruğa geri alınır (Redis kuyrukta zaten durur)."""
        from .domain.entities import RunStatus

        with self.uow() as uow:
            for run in uow.runs.list(limit=500):
                if run.status == RunStatus.RUNNING:
                    run.status = RunStatus.QUEUED
                    run.progress.message = "Sunucu yeniden başladı; iş yeniden sıraya alındı"
                    uow.runs.update(run)
            uow.commit()
            queued = uow.runs.queued()
        if self.settings.queue_backend == "inprocess":
            for run in queued:
                self.queue.enqueue(run.id)

    def local_actor(self) -> User:
        with self.uow() as uow:
            return uow.users.get_by_email(LOCAL_ADMIN_EMAIL)


@lru_cache
def get_container() -> Container:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    return Container(settings)
