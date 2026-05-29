"""Corpus smoke tests for the algorithmic engine.

A handful of canonical scenarios — one per shape × hotels axis — run
end-to-end through :func:`update_route` and scored with
:func:`score_scenario`. They guard the classifier + hotel pipeline against
regressions during rule iteration without standing in for the full 192-row
sweep (that's what ``just spike-engine-algo`` is for).

Slice 3 seeded three no-hotel forward-ordering scenarios (straight / circle
/ star). Slice 4 adds the three matching forward-ordering hotel scenarios so
the smoke covers the hotel-booking pipeline (per-stop accommodation + the
hotel-only intake + per-traveler accommodation slot) end-to-end.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from spikes.route_engine_algorithmic.engine import update_route
from spikes.route_engine_llm.corpus import load_scenario
from spikes.route_engine_llm.models import WorkingRoute
from spikes.route_engine_llm.scoring import score_scenario


@pytest.mark.parametrize(
    "scenario_name",
    [
        # Slice 3 — no-hotel forward scenarios (one per shape).
        "000-straight-1p-forward",
        "064-circle-1p-forward",
        "128-star-1p-forward",
        # Slice 4 — forward-ordering hotel scenarios (one per shape, with a
        # multi-pax variant so the add_travelers wiring is covered too).
        "020-straight-2p-forward-hotels",
        "068-circle-1p-forward-hotels",
        "132-star-1p-forward-hotels",
    ],
)
def test_algorithmic_engine_passes_canonical_forward_scenario(
    scenario_name: str,
) -> None:
    """Replay one canonical scenario; the three scoring checks must all pass."""
    scenario = load_scenario(scenario_name)
    route = WorkingRoute()
    snapshots: list[WorkingRoute] = []

    for fragment in scenario.fragments:
        update_route(route, fragment)
        snapshots.append(deepcopy(route))

    result = score_scenario(snapshots, scenario.expected)
    assert result.passed, (
        f"scenario {scenario_name!r} failed: {result.category} — {result.reason}"
    )
