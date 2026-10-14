"""GitHub webhook signature and request validation."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from typing import Any, Literal, NoReturn

from arq.connections import ArqRedis
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .config import Settings
from .persistence import PersistenceResult, PullRequestRecord, persist_pull_request
from .queue import enqueue_run

logger = logging.getLogger(__name__)

MAX_DELIVERY_ID_LENGTH = 255
_SIGNATURE_HEX = re.compile(r"^[0-9a-fA-F]{64}$")


def _reject_nonstandard_json_constant(value: str) -> NoReturn:
    raise ValueError(f"Invalid JSON constant: {value}")


class WebhookResponse(BaseModel):
    status: Literal["accepted", "duplicate", "ignored"]
    event: str
    delivery_id: str
    message: str


class WebhookError(BaseModel):
    code: str
    message: str


class WebhookErrorResponse(BaseModel):
    error: WebhookError


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    body = WebhookErrorResponse(error=WebhookError(code=code, message=message))
    return JSONResponse(status_code=status_code, content=body.model_dump())


def _signature_is_valid(header: str, secret: str, body: bytes) -> bool:
    prefix = "sha256="
    if not header.startswith(prefix):
        return False
    provided_hex = header[len(prefix) :]
    if not _SIGNATURE_HEX.fullmatch(provided_hex):
        return False

    expected_digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    provided_digest = bytes.fromhex(provided_hex)
    return hmac.compare_digest(expected_digest, provided_digest)


class PayloadValidationError(ValueError):
    """A signed pull-request event lacks fields needed for durable intake."""


def _positive_integer(value: Any, field: str, *, optional: bool = False) -> int | None:
    if optional and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PayloadValidationError(f"{field} must be a positive integer.")
    return int(value)


def _required_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PayloadValidationError(f"{field} must be an object.")
    return value


def _required_text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > maximum:
        raise PayloadValidationError(f"{field} must contain 1 to {maximum} valid characters.")
    return value


def _pull_request_record(payload: dict[str, Any]) -> PullRequestRecord:
    repository = _required_object(payload.get("repository"), "repository")
    pull_request = _required_object(payload.get("pull_request"), "pull_request")
    head = _required_object(pull_request.get("head"), "pull_request.head")
    repository_name = _required_text(repository.get("full_name"), "repository.full_name", 255)
    repository_id = _positive_integer(
        repository.get("id"), "repository.id", optional=True
    )
    pull_request_number = _positive_integer(pull_request.get("number"), "pull_request.number")
    head_sha = _required_text(head.get("sha"), "pull_request.head.sha", 128)
    base_sha: str | None = None
    base_value = pull_request.get("base")
    if base_value is not None:
        base = _required_object(base_value, "pull_request.base")
        base_sha = _required_text(base.get("sha"), "pull_request.base.sha", 128)
        if not re.fullmatch(r"[0-9a-fA-F]{40}", base_sha):
            raise PayloadValidationError("pull_request.base.sha must be a 40-character commit SHA.")

    installation_id: int | None = None
    installation_value = payload.get("installation")
    if installation_value is not None:
        installation = _required_object(installation_value, "installation")
        installation_id = _positive_integer(
            installation.get("id"), "installation.id", optional=True
        )

    assert pull_request_number is not None
    return PullRequestRecord(
        repository_full_name=repository_name,
        github_repository_id=repository_id,
        installation_id=installation_id,
        pull_request_number=pull_request_number,
        head_sha=head_sha,
        base_sha=base_sha,
    )


def create_webhook_router(settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/webhooks/github",
        response_model=WebhookResponse,
        responses={
            202: {"model": WebhookResponse, "description": "Delivery and run were persisted."},
            400: {"model": WebhookErrorResponse, "description": "Invalid headers or payload."},
            401: {"model": WebhookErrorResponse, "description": "Missing or invalid signature."},
            503: {"model": WebhookErrorResponse, "description": "Required service configuration is unavailable."},
        },
        tags=["webhooks"],
    )
    async def receive_github_webhook(request: Request) -> WebhookResponse | JSONResponse:
        secret = settings.github_webhook_secret
        if not secret:
            return _error(
                503,
                "webhook_not_configured",
                "GitHub webhook intake is not configured on this server.",
            )

        signature = request.headers.get("X-Hub-Signature-256")
        event = request.headers.get("X-GitHub-Event")
        raw_delivery_id = request.headers.get("X-GitHub-Delivery")

        if not event or not event.strip():
            return _error(400, "missing_header", "Required header X-GitHub-Event is missing.")
        if raw_delivery_id is None:
            return _error(400, "missing_header", "Required header X-GitHub-Delivery is missing.")

        delivery_id = raw_delivery_id
        if (
            not delivery_id.strip()
            or delivery_id != delivery_id.strip()
            or len(delivery_id) > MAX_DELIVERY_ID_LENGTH
        ):
            return _error(
                400,
                "invalid_delivery_id",
                f"X-GitHub-Delivery must contain 1 to {MAX_DELIVERY_ID_LENGTH} characters.",
            )

        if signature is None:
            return _error(401, "invalid_signature", "A valid X-Hub-Signature-256 header is required.")

        raw_body = await request.body()
        if not _signature_is_valid(signature, secret, raw_body):
            return _error(401, "invalid_signature", "Webhook signature is invalid.")

        try:
            payload = json.loads(raw_body, parse_constant=_reject_nonstandard_json_constant)
        except (ValueError, UnicodeDecodeError):
            return _error(400, "malformed_json", "Request body must contain valid JSON.")

        if not isinstance(payload, dict):
            return _error(400, "invalid_payload", "Webhook JSON payload must be an object.")

        normalized_event = event.strip()
        if normalized_event != "pull_request":
            return WebhookResponse(
                status="ignored",
                event=normalized_event,
                delivery_id=delivery_id,
                message="Event type is not supported; no analysis was run.",
            )

        try:
            pull_request = _pull_request_record(payload)
        except PayloadValidationError as error:
            return _error(400, "invalid_payload", str(error))

        session_factory: async_sessionmaker[AsyncSession] | None = (
            request.app.state.session_factory
        )
        if session_factory is None:
            return _error(
                503,
                "database_not_configured",
                "Pull-request intake requires DOCDRIFT_DATABASE_URL to be configured.",
            )

        try:
            result: PersistenceResult = await persist_pull_request(
                session_factory,
                delivery_id=delivery_id,
                pull_request=pull_request,
            )
        except (SQLAlchemyError, RuntimeError):
            return _error(
                503,
                "persistence_failed",
                "Webhook delivery could not be durably recorded; retry the delivery.",
            )

        if result.run_id is None:
            logger.error("persisted_delivery_missing_run")
            return _error(
                503,
                "persisted_run_missing",
                "The persisted webhook has no corresponding run; retry the delivery.",
            )

        redis_pool: ArqRedis | None = request.app.state.redis_pool
        if redis_pool is None:
            return _error(
                503,
                "redis_not_configured",
                "Pull-request intake requires DOCDRIFT_REDIS_URL to be configured.",
            )

        try:
            submitted = await enqueue_run(redis_pool, result.run_id)
        except Exception as error:
            # Do not log exception text: Redis clients may include connection details.
            logger.error(
                "run_queue_submission_failed",
                extra={"run_id": result.run_id, "error_type": type(error).__name__},
            )
            return _error(
                503,
                "queue_unavailable",
                "The run was persisted but could not be queued; retry the webhook delivery.",
            )

        if result.duplicate_delivery and not submitted:
            return WebhookResponse(
                status="duplicate",
                event=normalized_event,
                delivery_id=delivery_id,
                message="Webhook delivery was already recorded; no additional run was created.",
            )

        return JSONResponse(
            status_code=202,
            content=WebhookResponse(
                status="accepted",
                event=normalized_event,
                delivery_id=delivery_id,
                message="Webhook delivery and run were persisted and queued; analysis has not run.",
            ).model_dump(),
        )

    return router
