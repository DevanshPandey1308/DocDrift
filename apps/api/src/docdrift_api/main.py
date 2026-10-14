"""FastAPI application setup."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.ext.asyncio.engine import AsyncEngine

from .config import Settings
from .database import create_session_factory, dispose_engine
from .routes import create_health_router
from .webhooks import create_webhook_router


def create_app(
    settings: Settings | None = None,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> FastAPI:
    """Create an API app using explicit or environment-backed settings."""
    app_settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        engine: AsyncEngine | None = None
        application.state.session_factory = session_factory
        if session_factory is None and app_settings.database_url:
            application.state.session_factory, engine = create_session_factory(
                app_settings.database_url
            )
        try:
            yield
        finally:
            if engine is not None:
                await dispose_engine(engine)

    application = FastAPI(title=app_settings.title, version=app_settings.version, lifespan=lifespan)
    application.state.session_factory = session_factory
    application.include_router(create_health_router(app_settings))
    application.include_router(create_webhook_router(app_settings))
    return application


app = create_app()
