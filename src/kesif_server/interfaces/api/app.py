"""FastAPI uygulama fabrikası."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from ... import __version__
from ...container import Container, get_container
from .errors import install_error_handlers
from .middleware import RateLimitMiddleware, RequestContextMiddleware
from .v1.routers import auth, catalog, diseases, runs, system


def create_app(container: Optional[Container] = None) -> FastAPI:
    container = container or get_container()
    s = container.settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        container.startup()
        yield

    app = FastAPI(
        title=s.app_name,
        version=__version__,
        description="Literatür tabanlı ilaç yeniden konumlandırma platformu — REST API v1",
        docs_url=f"{s.api_prefix}/docs" if s.docs_enabled else None,
        redoc_url=None,
        openapi_url=f"{s.api_prefix}/openapi.json" if s.docs_enabled else None,
        lifespan=lifespan,
    )
    app.state.container = container

    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(RateLimitMiddleware, per_minute=s.rate_limit_per_minute)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
                       expose_headers=["X-Request-ID", "Content-Disposition"])
    install_error_handlers(app)

    for r in (system.router, auth.router, diseases.router, runs.router, catalog.router):
        app.include_router(r, prefix=s.api_prefix)
    return app
