"""Extraction tests are pure-Python: no DB, no FastAPI, no AWS.

The backend's top-level ``tests/conftest.py`` declares session-scoped, autouse
fixtures that require a live Postgres (skip-the-suite + apply-migrations). We
override them here as inert no-ops so the extraction tests run without any DB.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def _require_postgres() -> None:  # type: ignore[override]
    """No-op override: extraction tests never touch Postgres."""
    return None


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:  # type: ignore[override]
    """No-op override: extraction tests have no schema to migrate."""
    return None
