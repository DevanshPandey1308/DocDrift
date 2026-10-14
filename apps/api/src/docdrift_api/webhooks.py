"""GitHub webhook signature and request validation."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Literal, NoReturn

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .config import Settings

MAX_DELIVERY_ID_LENGTH = 255
_SIGNATURE_HEX = re.compile(r"^[0-9a-fA-F]{64}$")


def _reject_nonstandard_json_constant(value: str) -> NoReturn:
    raise ValueError(f"Invalid JSON constant: {value}")


class WebhookResponse(BaseModel):
    status: Literal["validated", "ignored"]
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


def create_webhook_router(settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/webhooks/github",
        response_model=WebhookResponse,
        responses={
            400: {"model": WebhookErrorResponse, "description": "Invalid headers or payload."},
            401: {"model": WebhookErrorResponse, "description": "Missing or invalid signature."},
            503: {"model": WebhookErrorResponse, "description": "Webhook secret is not configured."},
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

        return WebhookResponse(
            status="validated",
            event=normalized_event,
            delivery_id=delivery_id,
            message="Webhook validated; no analysis was run or queued.",
        )

    return router
