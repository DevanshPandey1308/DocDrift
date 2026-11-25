FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 docdrift

WORKDIR /app

COPY pyproject.toml README.md ./
COPY packages ./packages
COPY apps ./apps

RUN python -m pip install --upgrade pip \
    && python -m pip install ".[api,llm]"

USER docdrift

EXPOSE 8000
