"""Integration test for the /health endpoint against a real Postgres."""

from __future__ import annotations

from httpx import AsyncClient


async def test_health_returns_ok_when_db_is_up(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
