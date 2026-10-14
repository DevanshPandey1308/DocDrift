"""Environment-backed API settings."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_TITLE = "DocDrift API"
DEFAULT_VERSION = "0.1.0"


@dataclass(frozen=True)
class Settings:
    """Configuration required to initialize the API."""

    title: str = DEFAULT_TITLE
    version: str = DEFAULT_VERSION

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from supported environment variables."""
        return cls(
            title=os.getenv("DOCDRIFT_API_TITLE", DEFAULT_TITLE).strip() or DEFAULT_TITLE,
            version=os.getenv("DOCDRIFT_API_VERSION", DEFAULT_VERSION).strip() or DEFAULT_VERSION,
        )
