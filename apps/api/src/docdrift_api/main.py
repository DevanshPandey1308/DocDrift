"""FastAPI application setup."""

from __future__ import annotations

from fastapi import FastAPI

from .config import Settings
from .routes import create_health_router
from .webhooks import create_webhook_router


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create an API app using explicit or environment-backed settings."""
    app_settings = settings or Settings.from_env()
    application = FastAPI(title=app_settings.title, version=app_settings.version)
    application.include_router(create_health_router(app_settings))
    application.include_router(create_webhook_router(app_settings))
    return application


app = create_app()
