"""GitHub OAuth session and repository-authorized feedback API."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .config import Settings
from .feedback import FeedbackIdempotencyConflict, record_feedback
from .models import (
    AnalysisFinding,
    DashboardSession,
    FeedbackCategory,
    FeedbackEvent,
    PublishedSuggestion,
    Repository,
    Run,
)

SESSION_COOKIE = "docdrift_session"
CSRF_COOKIE = "docdrift_csrf"
SESSION_TTL = timedelta(hours=8)
MAX_PAGE_SIZE = 100
_REPOSITORY_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["fix_accepted", "fix_rejected", "false_positive"]
    idempotency_key: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")
    reason: str | None = Field(default=None, max_length=4000)


class FeedbackResponse(BaseModel):
    id: int
    finding_id: int
    run_id: int
    repository_id: int
    category: str
    reason: str | None
    actor_github_user_id: int | None
    created_at: datetime


class FeedbackPage(BaseModel):
    items: list[FeedbackResponse]
    limit: int
    offset: int


class MetricsResponse(BaseModel):
    accepted_fixes: int
    rejected_fixes: int
    false_positives: int
    numerator: int
    denominator: int
    acceptance_rate: float | None


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _fernet(settings: Settings) -> Fernet:
    key = settings.dashboard_session_encryption_key
    if key is None:
        raise HTTPException(503, "Dashboard authentication is not configured.")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as error:
        raise HTTPException(503, "Dashboard authentication is not configured.") from error


def _state(settings: Settings, nonce: str, issued: int) -> str:
    key = settings.dashboard_session_encryption_key
    if key is None:
        raise HTTPException(503, "Dashboard authentication is not configured.")
    mac = hmac.new(key.encode("utf-8"), f"{nonce}.{issued}".encode(), hashlib.sha256).hexdigest()
    return f"{nonce}.{issued}.{mac}"


def _valid_state(settings: Settings, value: str, now: int | None = None) -> bool:
    try:
        nonce, issued_text, mac = value.split(".", 2)
        issued = int(issued_text)
        expected = _state(settings, nonce, issued).rsplit(".", 1)[1]
        current = int(time.time()) if now is None else now
        return 0 <= current - issued <= 600 and hmac.compare_digest(
            mac.encode("utf-8"), expected.encode("ascii")
        )
    except (ValueError, HTTPException):
        return False


def _configured(settings: Settings) -> bool:
    if not (
        settings.github_oauth_client_id
        and settings.github_oauth_client_secret
        and settings.dashboard_session_encryption_key
        and (settings.dashboard_oauth_redirect_uri.startswith("https://")
             or settings.dashboard_cookie_secure is False)
    ):
        return False
    try:
        Fernet(settings.dashboard_session_encryption_key.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return False
    return True


async def _get_session(request: Request) -> tuple[DashboardSession, str]:
    settings: Settings = request.app.state.settings
    session_factory: async_sessionmaker[AsyncSession] | None = request.app.state.session_factory
    raw = request.cookies.get(SESSION_COOKIE)
    if session_factory is None or not raw or len(raw) > 128:
        raise HTTPException(401, "Authentication required.")
    async with session_factory() as session:
        record = await session.scalar(
            select(DashboardSession).where(
                DashboardSession.token_hash == _digest(raw),
                DashboardSession.expires_at > datetime.now(UTC),
            )
        )
        if record is None:
            raise HTTPException(401, "Authentication required.")
        try:
            token = _fernet(settings).decrypt(record.encrypted_access_token.encode()).decode()
        except (InvalidToken, UnicodeDecodeError, HTTPException):
            raise HTTPException(401, "Authentication required.") from None
        return record, token


async def _csrf(request: Request, x_csrf_token: Annotated[str | None, Header()] = None) -> None:
    cookie = request.cookies.get(CSRF_COOKIE)
    if (
        not cookie
        or not x_csrf_token
        or not hmac.compare_digest(cookie.encode("utf-8"), x_csrf_token.encode("utf-8"))
    ):
        raise HTTPException(403, "CSRF validation failed.")
    record, _ = await _get_session(request)
    if not hmac.compare_digest(record.csrf_token_hash, _digest(cookie)):
        raise HTTPException(403, "CSRF validation failed.")


async def _authorized_repository(
    request: Request, repository_id: int, token: str
) -> Repository:
    session_factory: async_sessionmaker[AsyncSession] | None = request.app.state.session_factory
    assert session_factory is not None
    async with session_factory() as session:
        repo = await session.scalar(
            select(Repository).where(
                Repository.id == repository_id,
                Repository.github_repository_id.is_not(None),
                Repository.installation_id.is_not(None),
            )
        )
        if repo is None:
            raise HTTPException(404, "Resource not found.")
        full_name = repo.full_name
        github_id = repo.github_repository_id
    if not _REPOSITORY_NAME.fullmatch(full_name):
        raise HTTPException(404, "Resource not found.")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5), follow_redirects=False) as client:
            result = await client.get(
                f"https://api.github.com/repos/{full_name}",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            )
    except httpx.HTTPError:
        raise HTTPException(503, "Repository authorization could not be checked.") from None
    if result.status_code != 200:
        raise HTTPException(404, "Resource not found.")
    try:
        body = result.json()
    except ValueError:
        raise HTTPException(503, "Repository authorization could not be checked.") from None
    if (
        not isinstance(body, dict)
        or body.get("id") != github_id
        or not isinstance(body.get("full_name"), str)
        or body["full_name"].casefold() != full_name.casefold()
    ):
        raise HTTPException(404, "Resource not found.")
    return repo


def create_dashboard_router(settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.get("/auth/github/login", tags=["dashboard-auth"])
    async def login() -> Response:
        if not _configured(settings):
            raise HTTPException(503, "Dashboard authentication is not configured.")
        nonce = secrets.token_urlsafe(32)
        state = _state(settings, nonce, int(time.time()))
        response = RedirectResponse(
            "https://github.com/login/oauth/authorize?"
            + urlencode({"client_id": settings.github_oauth_client_id, "redirect_uri": settings.dashboard_oauth_redirect_uri, "state": state, "scope": "repo read:org"}),
            status_code=302,
        )
        response.set_cookie("docdrift_oauth_state", state, max_age=600, httponly=True,
                            secure=settings.dashboard_cookie_secure, samesite="lax", path="/auth/github")
        return response

    @router.get("/auth/github/callback", tags=["dashboard-auth"])
    async def callback(
        request: Request,
        code: Annotated[str, Query(min_length=1, max_length=2048)],
        state: Annotated[str, Query(min_length=1, max_length=1024)],
    ) -> Response:
        if not _configured(settings):
            raise HTTPException(503, "Dashboard authentication is not configured.")
        cookie_state = request.cookies.get("docdrift_oauth_state", "")
        if (
            not hmac.compare_digest(cookie_state.encode("utf-8"), state.encode("utf-8"))
            or not _valid_state(settings, state)
        ):
            raise HTTPException(400, "OAuth state validation failed.")
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5), follow_redirects=False) as client:
                token_result = await client.post(
                    "https://github.com/login/oauth/access_token",
                    headers={"Accept": "application/json"},
                    json={"client_id": settings.github_oauth_client_id,
                          "client_secret": settings.github_oauth_client_secret, "code": code,
                          "redirect_uri": settings.dashboard_oauth_redirect_uri},
                )
                token_result.raise_for_status()
                token_body = token_result.json()
                access_token = token_body.get("access_token") if isinstance(token_body, dict) else None
                if not isinstance(access_token, str) or not access_token:
                    raise ValueError("OAuth token missing")
                identity = await client.get(
                    "https://api.github.com/user",
                    headers={"Authorization": f"Bearer {access_token}", "Accept": "application/vnd.github+json"},
                )
                identity.raise_for_status()
                user = identity.json()
                if not isinstance(user, dict):
                    raise TypeError("Invalid GitHub identity")
                user_id, login_name = user.get("id"), user.get("login")
                if (
                    isinstance(user_id, bool)
                    or not isinstance(user_id, int)
                    or user_id < 1
                    or not isinstance(login_name, str)
                    or not login_name
                    or len(login_name) > 255
                ):
                    raise ValueError("Invalid GitHub identity")
        except (httpx.HTTPError, ValueError, TypeError):
            raise HTTPException(401, "GitHub authentication failed.") from None
        factory = request.app.state.session_factory
        if factory is None:
            raise HTTPException(503, "Database is unavailable.")
        session_token, csrf_token = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        async with factory() as session, session.begin():
            session.add(DashboardSession(
                token_hash=_digest(session_token), github_user_id=user_id, github_login=login_name,
                encrypted_access_token=_fernet(settings).encrypt(access_token.encode()).decode(),
                csrf_token_hash=_digest(csrf_token), expires_at=datetime.now(UTC) + SESSION_TTL,
            ))
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie("docdrift_oauth_state", path="/auth/github")
        response.set_cookie(SESSION_COOKIE, session_token, max_age=int(SESSION_TTL.total_seconds()),
                            httponly=True, secure=settings.dashboard_cookie_secure, samesite="lax", path="/")
        response.set_cookie(CSRF_COOKIE, csrf_token, max_age=int(SESSION_TTL.total_seconds()),
                            httponly=False, secure=settings.dashboard_cookie_secure, samesite="lax", path="/")
        return response

    @router.post("/suggestions/{suggestion_id}/feedback", response_model=FeedbackResponse, tags=["feedback"])
    async def suggestion_feedback(suggestion_id: int, body: FeedbackRequest, request: Request,
                                  _check: None = Depends(_csrf)) -> FeedbackResponse:
        session_record, token = await _get_session(request)
        factory = request.app.state.session_factory
        async with factory() as session:
            suggestion = await session.scalar(select(PublishedSuggestion).where(
                PublishedSuggestion.id == suggestion_id,
                PublishedSuggestion.publication_status == "published",
            ))
        if suggestion is None:
            raise HTTPException(404, "Resource not found.")
        await _authorized_repository(request, suggestion.repository_id, token)
        if body.category == "false_positive":
            raise HTTPException(422, "Use the finding feedback endpoint for false-positive reports.")
        try:
            event = await record_feedback(
                factory, finding_id=suggestion.finding_id, run_id=suggestion.run_id,
                repository_id=suggestion.repository_id, category=FeedbackCategory(body.category),
                source="dashboard", source_event_id=f"{session_record.github_user_id}:{body.idempotency_key}",
                reason=body.reason, actor_github_user_id=session_record.github_user_id,
            )
        except FeedbackIdempotencyConflict:
            raise HTTPException(409, "Idempotency key was used for another feedback event.") from None
        return FeedbackResponse.model_validate(event, from_attributes=True)

    @router.post("/findings/{finding_id}/feedback", response_model=FeedbackResponse, tags=["feedback"])
    async def finding_feedback(finding_id: int, body: FeedbackRequest, request: Request,
                               _check: None = Depends(_csrf)) -> FeedbackResponse:
        session_record, token = await _get_session(request)
        factory = request.app.state.session_factory
        async with factory() as session:
            finding = await session.scalar(select(AnalysisFinding).where(AnalysisFinding.id == finding_id))
            if finding is None:
                raise HTTPException(404, "Resource not found.")
            run = await session.get(Run, finding.run_id)
            if run is None:
                raise HTTPException(404, "Resource not found.")
            repository_id = run.repository_id
        await _authorized_repository(request, repository_id, token)
        if body.category != "false_positive":
            raise HTTPException(422, "Accepted/rejected feedback requires a published suggestion.")
        try:
            event = await record_feedback(
                factory, finding_id=finding_id, run_id=run.id, repository_id=repository_id,
                category=FeedbackCategory.FALSE_POSITIVE, source="dashboard",
                source_event_id=f"{session_record.github_user_id}:{body.idempotency_key}",
                reason=body.reason, actor_github_user_id=session_record.github_user_id,
            )
        except FeedbackIdempotencyConflict:
            raise HTTPException(409, "Idempotency key was used for another feedback event.") from None
        return FeedbackResponse.model_validate(event, from_attributes=True)

    @router.get("/findings/{finding_id}/feedback", response_model=FeedbackPage, tags=["feedback"])
    async def finding_feedback_list(finding_id: int, request: Request,
                                    limit: int = 50, offset: int = 0) -> FeedbackPage:
        _, token = await _get_session(request)
        if not 1 <= limit <= MAX_PAGE_SIZE or offset < 0:
            raise HTTPException(422, "Invalid pagination parameters.")
        factory = request.app.state.session_factory
        async with factory() as session:
            finding = await session.scalar(select(AnalysisFinding).where(AnalysisFinding.id == finding_id))
            if finding is None:
                raise HTTPException(404, "Resource not found.")
            run = await session.get(Run, finding.run_id)
            if run is None:
                raise HTTPException(404, "Resource not found.")
            run_id, repository_id = run.id, run.repository_id
        await _authorized_repository(request, repository_id, token)
        async with factory() as session:
            rows = await session.scalars(select(FeedbackEvent).where(
                FeedbackEvent.finding_id == finding_id,
                FeedbackEvent.run_id == run_id,
                FeedbackEvent.repository_id == repository_id,
            ).order_by(FeedbackEvent.created_at, FeedbackEvent.id).limit(limit).offset(offset))
            items = [FeedbackResponse.model_validate(item, from_attributes=True) for item in rows]
        return FeedbackPage(items=items, limit=limit, offset=offset)

    @router.get("/runs/{run_id}/feedback", response_model=FeedbackPage, tags=["feedback"])
    async def run_feedback(run_id: int, request: Request, limit: int = 50, offset: int = 0) -> FeedbackPage:
        _, token = await _get_session(request)
        if not 1 <= limit <= MAX_PAGE_SIZE or offset < 0:
            raise HTTPException(422, "Invalid pagination parameters.")
        factory = request.app.state.session_factory
        async with factory() as session:
            run = await session.get(Run, run_id)
            if run is None:
                raise HTTPException(404, "Resource not found.")
            repo_id = run.repository_id
        await _authorized_repository(request, repo_id, token)
        async with factory() as session:
            rows = await session.scalars(select(FeedbackEvent).where(
                FeedbackEvent.run_id == run_id, FeedbackEvent.repository_id == repo_id
            ).order_by(FeedbackEvent.created_at, FeedbackEvent.id).limit(limit).offset(offset))
            items = [FeedbackResponse.model_validate(item, from_attributes=True) for item in rows]
        return FeedbackPage(items=items, limit=limit, offset=offset)

    @router.get("/repositories/{repository_id}/feedback/metrics", response_model=MetricsResponse, tags=["feedback"])
    async def repository_metrics(repository_id: int, request: Request) -> MetricsResponse:
        _, token = await _get_session(request)
        await _authorized_repository(request, repository_id, token)
        factory = request.app.state.session_factory
        async with factory() as session:
            accepted = int(await session.scalar(select(func.count()).select_from(FeedbackEvent).where(
                FeedbackEvent.repository_id == repository_id, FeedbackEvent.category == FeedbackCategory.FIX_ACCEPTED)) or 0)
            rejected = int(await session.scalar(select(func.count()).select_from(FeedbackEvent).where(
                FeedbackEvent.repository_id == repository_id, FeedbackEvent.category == FeedbackCategory.FIX_REJECTED)) or 0)
            false_positives = int(await session.scalar(select(func.count()).select_from(FeedbackEvent).where(
                FeedbackEvent.repository_id == repository_id, FeedbackEvent.category == FeedbackCategory.FALSE_POSITIVE)) or 0)
        denominator = accepted + rejected
        return MetricsResponse(accepted_fixes=accepted, rejected_fixes=rejected,
                               false_positives=false_positives, numerator=accepted,
                               denominator=denominator,
                               acceptance_rate=accepted / denominator if denominator else None)

    @router.post("/auth/logout", status_code=204, tags=["dashboard-auth"])
    async def logout(request: Request, response: Response,
                     _check: None = Depends(_csrf)) -> Response:
        record, _ = await _get_session(request)
        factory = request.app.state.session_factory
        async with factory() as session, session.begin():
            current = await session.get(DashboardSession, record.id)
            if current is not None:
                await session.delete(current)
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")
        return response

    return router
