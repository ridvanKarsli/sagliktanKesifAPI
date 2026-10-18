from __future__ import annotations

import pytest

from kesif_server.core.settings import Settings


def test_cors_env_formats(monkeypatch):
    monkeypatch.setenv("KESIF_CORS_ORIGINS", "http://a, http://b")
    assert Settings().cors_origins == ["http://a", "http://b"]
    monkeypatch.setenv("KESIF_CORS_ORIGINS", '["http://x"]')
    assert Settings().cors_origins == ["http://x"]


def test_prod_refuses_unsafe_defaults():
    with pytest.raises(RuntimeError, match="SQLite"):
        Settings(env="prod").validate_for_production()
    Settings(env="prod", database_url="postgresql+psycopg://u:p@h/db", queue_backend="redis", jwt_secret="x" * 40).validate_for_production()
