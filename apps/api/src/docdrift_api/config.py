"""Environment-backed API settings."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

DEFAULT_TITLE = "DocDrift API"
DEFAULT_VERSION = "0.1.0"
MAX_LLM_RUN_TOKEN_BUDGET = 100_000


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
    llm_enabled: bool = False
    anthropic_api_key: str | None = field(default=None, repr=False)
    anthropic_verification_model: str = ""
    anthropic_fix_model: str = ""
    llm_timeout_seconds: float = 20.0
    llm_run_token_budget: int = 20_000
    github_oauth_client_id: str | None = None
    github_oauth_client_secret: str | None = field(default=None, repr=False)
    dashboard_session_encryption_key: str | None = field(default=None, repr=False)
    dashboard_oauth_redirect_uri: str = "http://localhost:8000/auth/github/callback"
    dashboard_cookie_secure: bool = True

    def __post_init__(self) -> None:
        if (
            isinstance(self.llm_timeout_seconds, bool)
            or not isinstance(self.llm_timeout_seconds, (int, float))
            or not math.isfinite(self.llm_timeout_seconds)
            or not 0 < self.llm_timeout_seconds <= 60
        ):
            raise ValueError("LLM timeout must be greater than zero and at most 60 seconds.")
        if (
            isinstance(self.llm_run_token_budget, bool)
            or not isinstance(self.llm_run_token_budget, int)
            or not 1 <= self.llm_run_token_budget <= MAX_LLM_RUN_TOKEN_BUDGET
        ):
            raise ValueError(
                f"LLM run token budget must be between 1 and {MAX_LLM_RUN_TOKEN_BUDGET}."
            )

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from supported environment variables."""
        webhook_secret = os.getenv("DOCDRIFT_GITHUB_WEBHOOK_SECRET")
        app_id = os.getenv("DOCDRIFT_GITHUB_APP_ID")
        app_private_key = os.getenv("DOCDRIFT_GITHUB_APP_PRIVATE_KEY")
        database_url = os.getenv("DOCDRIFT_DATABASE_URL")
        redis_url = os.getenv("DOCDRIFT_REDIS_URL")
        llm_enabled = os.getenv("DOCDRIFT_LLM_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
        api_key = os.getenv("ANTHROPIC_API_KEY")
        try:
            timeout = float(os.getenv("DOCDRIFT_LLM_TIMEOUT_SECONDS", "20"))
            token_budget = int(os.getenv("DOCDRIFT_LLM_RUN_TOKEN_BUDGET", "20000"))
        except ValueError as error:
            raise ValueError("LLM timeout and run token budget must be numeric.") from error
        if not 0 < timeout <= 60 or not 1 <= token_budget <= MAX_LLM_RUN_TOKEN_BUDGET:
            raise ValueError("LLM timeout or run token budget is outside its supported bounds.")
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
            llm_enabled=llm_enabled,
            anthropic_api_key=api_key.strip() if api_key and api_key.strip() else None,
            anthropic_verification_model=os.getenv("DOCDRIFT_ANTHROPIC_VERIFICATION_MODEL", "").strip(),
            anthropic_fix_model=os.getenv("DOCDRIFT_ANTHROPIC_FIX_MODEL", "").strip(),
            llm_timeout_seconds=timeout,
            llm_run_token_budget=token_budget,
            github_oauth_client_id=(os.getenv("DOCDRIFT_GITHUB_OAUTH_CLIENT_ID") or "").strip() or None,
            github_oauth_client_secret=(os.getenv("DOCDRIFT_GITHUB_OAUTH_CLIENT_SECRET") or "").strip() or None,
            dashboard_session_encryption_key=(os.getenv("DOCDRIFT_SESSION_ENCRYPTION_KEY") or "").strip() or None,
            dashboard_oauth_redirect_uri=(
                os.getenv("DOCDRIFT_GITHUB_OAUTH_REDIRECT_URI", "http://localhost:8000/auth/github/callback").strip()
            ),
            dashboard_cookie_secure=os.getenv("DOCDRIFT_DASHBOARD_COOKIE_SECURE", "true").strip().lower()
            not in {"0", "false", "no"},
        )
