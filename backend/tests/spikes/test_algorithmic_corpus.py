"""Corpus smoke tests for the algorithmic engine.

Three canonical no-hotel, forward-ordering scenarios — one per shape — run
end-to-end through :func:`update_route` and scored with
:func:`score_scenario`. These guard the Slice-3 classifier against
regressions during rule iteration. Hotel + reverse / bisect / shuffle
variants land in Slice 4.
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
        "000-straight-1p-forward",
        "064-circle-1p-forward",
        "128-star-1p-forward",
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
