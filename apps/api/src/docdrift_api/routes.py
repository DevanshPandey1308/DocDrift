"""HTTP route definitions for the API adapter."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from .config import Settings

SERVICE_NAME = "docdrift-api"


class HealthResponse(BaseModel):
    status: Literal["healthy"]
    service: str
    version: str


def create_health_router(settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.get("/health", response_model=HealthResponse, tags=["health"])
    async def health() -> HealthResponse:
        return HealthResponse(status="healthy", service=SERVICE_NAME, version=settings.version)

    return router
