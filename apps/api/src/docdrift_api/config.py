"""Environment-backed API settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_TITLE = "DocDrift API"
DEFAULT_VERSION = "0.1.0"


@dataclass(frozen=True)
class Settings:
    """Configuration required to initialize the API."""

    title: str = DEFAULT_TITLE
    version: str = DEFAULT_VERSION
    github_webhook_secret: str | None = field(default=None, repr=False)
    github_app_id: str | None = None
    github_app_private_key: str | None = field(default=None, repr=False)
    database_url: str | None = field(default=None, repr=False)
    redis_url: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from supported environment variables."""
        webhook_secret = os.getenv("DOCDRIFT_GITHUB_WEBHOOK_SECRET")
        app_id = os.getenv("DOCDRIFT_GITHUB_APP_ID")
        app_private_key = os.getenv("DOCDRIFT_GITHUB_APP_PRIVATE_KEY")
        database_url = os.getenv("DOCDRIFT_DATABASE_URL")
        redis_url = os.getenv("DOCDRIFT_REDIS_URL")
        return cls(
            title=os.getenv("DOCDRIFT_API_TITLE", DEFAULT_TITLE).strip() or DEFAULT_TITLE,
            version=os.getenv("DOCDRIFT_API_VERSION", DEFAULT_VERSION).strip() or DEFAULT_VERSION,
            github_webhook_secret=webhook_secret
            if webhook_secret and webhook_secret.strip()
            else None,
            github_app_id=app_id.strip() if app_id and app_id.strip() else None,
            github_app_private_key=(
                app_private_key.replace("\\n", "\n")
                if app_private_key and app_private_key.strip()
                else None
            ),
            database_url=database_url.strip() if database_url and database_url.strip() else None,
            redis_url=redis_url.strip() if redis_url and redis_url.strip() else None,
        )
