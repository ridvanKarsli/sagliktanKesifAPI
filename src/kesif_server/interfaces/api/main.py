"""ASGI giriş noktası: `uvicorn kesif_server.interfaces.api.main:app`."""

from .app import create_app

app = create_app()
