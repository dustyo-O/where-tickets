"""Corpus tests are pure subprocess-driven: no DB, no FastAPI, no AWS.

The backend's top-level ``tests/conftest.py`` declares session-scoped, autouse
fixtures that require a live Postgres (skip-the-suite + apply-migrations). We
override them here as inert no-ops so the corpus tests run without any DB.

DUS-31 Slice 8: the integration trip-bundle generator lives under
``corpus/integration/generator/``. To import it in-process from tests, we put
the repo root on ``sys.path``. ``corpus`` resolves as a PEP 420 namespace
package (no ``__init__.py`` at the repo's ``corpus/`` directory), so this
just makes ``corpus.integration.generator`` discoverable.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture(scope="session", autouse=True)
def _require_postgres() -> None:  # type: ignore[override]
    """No-op override: corpus tests never touch Postgres."""
    return None


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:  # type: ignore[override]
    """No-op override: corpus tests have no schema to migrate."""
    return None
