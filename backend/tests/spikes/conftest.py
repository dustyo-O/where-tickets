"""Spike tests are pure in-memory: no DB, no FastAPI, no AWS.

The backend's top-level ``tests/conftest.py`` declares session-scoped, autouse
fixtures that require a live Postgres (skip-the-suite + apply-migrations). We
override them here as inert no-ops so the spike's offline unit tests run
without any database, satisfying the spike's "no DB" guarantee.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def _require_postgres() -> None:  # type: ignore[override]
    """No-op override: spike tests never touch Postgres."""
    return None


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:  # type: ignore[override]
    """No-op override: spike tests have no schema to migrate."""
    return None
