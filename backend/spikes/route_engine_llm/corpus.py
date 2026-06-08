"""Load corpus scenarios: ordered fragments + the expected route.

A scenario lives under ``corpus/scenarios/<name>/`` with:

- ``fragments/NN-*.json`` — input documents, replayed in filename order
  (``01-…`` before ``02-…``), each parsed into the Slice 1 ``Fragment`` union.
- ``expected-route.json`` — the canonical route the engine should produce,
  parsed into :class:`ExpectedRoute` (mirrors ``expected-route.schema.json``).

This module is pure I/O over the committed corpus — no DB, no network. The
corpus root is located relative to the repo, independent of the CWD.
"""

from __future__ import annotations

import json
from datetime import datetime
from functools import cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from spikes.route_engine_llm.models import Accommodation, Fragment, TransitMode

__all__ = [
    "ExpectedStop",
    "ExpectedTransit",
    "ExpectedRoute",
    "Scenario",
    "corpus_root",
    "scenario_dir",
    "load_scenario",
]


# --------------------------------------------------------------------------- #
# Expected-route models (mirror corpus/schema/expected-route.schema.json)
# --------------------------------------------------------------------------- #


class ExpectedStop(BaseModel):
    """One ordered stop in the expected route (no engine ID)."""

    model_config = ConfigDict(extra="forbid")

    city: str
    arrival_at: datetime | None = Field(default=None, alias="arrivalAt")
    departure_at: datetime | None = Field(default=None, alias="departureAt")
    travelers: list[str] = Field(min_length=1)
    accommodations: list[Accommodation] = Field(default_factory=list)


class ExpectedTransit(BaseModel):
    """One transit in the expected route, keyed by city codes (not IDs)."""

    model_config = ConfigDict(extra="forbid")

    from_: str = Field(alias="from")
    to: str
    mode: TransitMode
    departure_at: datetime = Field(alias="departureAt")
    arrival_at: datetime = Field(alias="arrivalAt")
    travelers: list[str] = Field(min_length=1)
    source_fragment_id: str = Field(alias="sourceFragmentId")


class ExpectedRoute(BaseModel):
    """The canonical composed route for a scenario."""

    model_config = ConfigDict(extra="forbid")

    travelers: list[str] = Field(min_length=1)
    stops: list[ExpectedStop] = Field(default_factory=list)
    transits: list[ExpectedTransit] = Field(default_factory=list)
    notes: str | None = None


# --------------------------------------------------------------------------- #
# Scenario container
# --------------------------------------------------------------------------- #


class Scenario(BaseModel):
    """A loaded scenario: its name, ordered fragments, and expected route."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    fragments: list[Fragment]
    expected: ExpectedRoute


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

# Reusable adapter for the discriminated `Fragment` union.
_FRAGMENT_ADAPTER: TypeAdapter[Fragment] = TypeAdapter(Fragment)


@cache
def corpus_root() -> Path:
    """Locate ``corpus/`` at the repo root, independent of the CWD.

    Resolves relative to this file: ``backend/spikes/route_engine_llm/`` is
    three levels under the repo root, which holds ``corpus/``.
    """
    root = Path(__file__).resolve().parents[3] / "corpus"
    if not (root / "scenarios").is_dir():
        msg = f"corpus scenarios directory not found under {root}"
        raise FileNotFoundError(msg)
    return root


def scenario_dir(name: str) -> Path:
    """Return the directory for scenario ``name`` under the corpus root."""
    path = corpus_root() / "scenarios" / name
    if not path.is_dir():
        msg = f"scenario directory not found: {path}"
        raise FileNotFoundError(msg)
    return path


def _load_fragments(directory: Path) -> list[Fragment]:
    """Parse ``fragments/*.json`` in filename order into the Fragment union."""
    fragments_dir = directory / "fragments"
    if not fragments_dir.is_dir():
        msg = f"scenario has no fragments directory: {fragments_dir}"
        raise FileNotFoundError(msg)
    paths = sorted(fragments_dir.glob("*.json"), key=lambda p: p.name)
    return [
        _FRAGMENT_ADAPTER.validate_python(json.loads(path.read_text("utf-8")))
        for path in paths
    ]


def _load_expected(directory: Path) -> ExpectedRoute:
    """Parse ``expected-route.json`` into :class:`ExpectedRoute`."""
    path = directory / "expected-route.json"
    if not path.is_file():
        msg = f"scenario has no expected-route.json: {path}"
        raise FileNotFoundError(msg)
    return ExpectedRoute.model_validate_json(path.read_text("utf-8"))


def load_scenario(name: str) -> Scenario:
    """Load the scenario ``name`` (ordered fragments + expected route)."""
    directory = scenario_dir(name)
    return Scenario(
        name=name,
        fragments=_load_fragments(directory),
        expected=_load_expected(directory),
    )
