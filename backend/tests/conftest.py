"""Pytest fixtures for backend integration tests.

These tests run against a real Postgres instance (started via `just dev` or
`docker compose up -d postgres` from the repo root). They do not mock the DB.
"""

from __future__ import annotations

import os
import socket
from collections.abc import AsyncIterator
from urllib.parse import urlparse

import pytest

# Default DSN mirrors docker-compose.yml so the suite works even without .env.
DEFAULT_DATABASE_URL = (
    "postgresql://where_tickets:where_tickets@localhost:5432/where_tickets"
)

# Ensure env is set BEFORE importing the app, since app.config reads it at
# module-load time via pydantic-settings.
os.environ.setdefault("DATABASE_URL", DEFAULT_DATABASE_URL)
os.environ.setdefault("APP_ENV", "local")
os.environ.setdefault("PICCOLO_CONF", "piccolo_conf")


def _postgres_reachable(dsn: str) -> bool:
    parsed = urlparse(dsn)
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session", autouse=True)
def _require_postgres() -> None:
    """Skip the whole suite if Postgres isn't reachable."""
    dsn = os.environ["DATABASE_URL"]
    if not _postgres_reachable(dsn):
        pytest.skip(
            f"Postgres is not reachable at {dsn}. "
            "Start it with `docker compose up -d postgres` from the repo root."
        )


@pytest.fixture(scope="session", autouse=True)
async def _apply_migrations(_require_postgres: None) -> None:
    """Apply Piccolo migrations once per session against the live DB."""
    from piccolo.apps.migrations.commands.forwards import run_forwards

    response = await run_forwards(app_name="all")
    if not response.success:
        raise RuntimeError(f"Piccolo migrations failed: {response.message}")


@pytest.fixture
async def client() -> AsyncIterator:
    """Async HTTP client wired to the FastAPI app with lifespan triggered."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
