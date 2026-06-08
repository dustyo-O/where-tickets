"""Offline tests for the runner CLI: import-safety, parsing, discovery, replay.

No network, no ``anthropic``, no AWS. Importing ``run`` must succeed without the
``spike`` group (its only ``anthropic`` dependency is the lazy import inside
``make_client``). ``run_scenario`` is exercised with a fake client so the replay
loop, snapshotting, scoring, and cost wiring are covered offline.
"""

from __future__ import annotations

from typing import Any

import pytest

from spikes.route_engine_llm import run as run_module
from spikes.route_engine_llm.bedrock_client import ToolUseResult, Usage
from spikes.route_engine_llm.corpus import ExpectedRoute, Scenario
from spikes.route_engine_llm.models import TransitTicketFragment
from spikes.route_engine_llm.pricing import resolve_price
from spikes.route_engine_llm.run import (
    RunnerError,
    build_parser,
    discover_scenarios,
    run_scenario,
)


# --------------------------------------------------------------------------- #
# CLI parsing (no I/O)
# --------------------------------------------------------------------------- #


def test_parser_accepts_model_and_filters() -> None:
    args = build_parser().parse_args(
        ["--model", "haiku", "--shape", "circle", "--limit", "3"]
    )
    assert args.model == "haiku"
    assert args.shape == "circle"
    assert args.limit == 3


def test_parser_rejects_unknown_model() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--model", "gpt"])


def test_parser_requires_model() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_help_is_side_effect_free() -> None:
    # argparse prints help and raises SystemExit(0); no network/AWS touched.
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--help"])
    assert excinfo.value.code == 0


# --------------------------------------------------------------------------- #
# Discovery + filtering against the real (committed) corpus
# --------------------------------------------------------------------------- #


def test_discover_all_returns_full_corpus_sorted() -> None:
    names = discover_scenarios()
    assert len(names) == 192
    assert names == sorted(names)
    assert names[0] == "000-straight-1p-forward"


def test_discover_limit_truncates_after_sorting() -> None:
    assert discover_scenarios(limit=3) == [
        "000-straight-1p-forward",
        "001-straight-1p-reverse",
        "002-straight-1p-bisect",
    ]


def test_discover_shape_filters_by_shape() -> None:
    circles = discover_scenarios(shape="circle")
    assert len(circles) == 64
    assert all(n.split("-")[1] == "circle" for n in circles)


def test_discover_single_scenario() -> None:
    assert discover_scenarios(scenario="000-straight-1p-forward") == [
        "000-straight-1p-forward"
    ]


def test_discover_unknown_scenario_raises() -> None:
    with pytest.raises(RunnerError, match="not found"):
        discover_scenarios(scenario="999-nope")


def test_discover_empty_selection_raises() -> None:
    with pytest.raises(RunnerError, match="no scenarios matched"):
        discover_scenarios(shape="circle", limit=0)


# --------------------------------------------------------------------------- #
# Per-scenario replay with a fake client (no network)
# --------------------------------------------------------------------------- #


class _ScriptedClient:
    """A fake client returning a queued op list per ``complete`` call."""

    def __init__(self, scripts: list[list[dict[str, Any]]]) -> None:
        self._scripts = list(scripts)
        self.calls = 0

    def complete(
        self,
        *,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any],
    ) -> ToolUseResult:
        ops = self._scripts[self.calls]
        self.calls += 1
        return ToolUseResult(
            tool_input={"operations": ops},
            usage=Usage(input_tokens=100, output_tokens=10),
            latency_seconds=0.25,
        )


def _one_leg_fragment() -> TransitTicketFragment:
    return TransitTicketFragment.model_validate(
        {
            "documentType": "bus-ticket",
            "sourceDocumentId": "tkt-01",
            "pnr": "ABC123",
            "travelers": ["traveler-1"],
            "cities": ["HEL", "ROM"],
            "stations": [
                {
                    "city": "HEL",
                    "kind": "bus_terminal",
                    "identifier": "HEL Bus Terminal",
                    "departureAt": "2027-03-01T00:00:00+00:00",
                },
                {
                    "city": "ROM",
                    "kind": "bus_terminal",
                    "identifier": "ROM Bus Terminal",
                    "arrivalAt": "2027-03-01T03:00:00+00:00",
                },
            ],
        }
    )


def _passing_scenario() -> Scenario:
    expected = ExpectedRoute.model_validate(
        {
            "travelers": ["traveler-1"],
            "stops": [
                {
                    "city": "HEL",
                    "departureAt": "2027-03-01T00:00:00+00:00",
                    "travelers": ["traveler-1"],
                },
                {
                    "city": "ROM",
                    "arrivalAt": "2027-03-01T03:00:00+00:00",
                    "travelers": ["traveler-1"],
                },
            ],
            "transits": [
                {
                    "from": "HEL",
                    "to": "ROM",
                    "mode": "bus",
                    "departureAt": "2027-03-01T00:00:00+00:00",
                    "arrivalAt": "2027-03-01T03:00:00+00:00",
                    "travelers": ["traveler-1"],
                    "sourceFragmentId": "tkt-01",
                }
            ],
        }
    )
    return Scenario(
        name="000-straight-1p-forward",
        fragments=[_one_leg_fragment()],
        expected=expected,
    )


def _build_ops() -> list[dict[str, Any]]:
    return [
        {"op": "create_stop", "city": "HEL"},
        {"op": "create_stop", "city": "ROM", "after": "stop-1"},
        {
            "op": "enrich_stop",
            "stopId": "stop-1",
            "departureAt": "2027-03-01T00:00:00+00:00",
        },
        {
            "op": "enrich_stop",
            "stopId": "stop-2",
            "arrivalAt": "2027-03-01T03:00:00+00:00",
        },
        {"op": "add_travelers", "stopId": "stop-1", "travelers": ["traveler-1"]},
        {"op": "add_travelers", "stopId": "stop-2", "travelers": ["traveler-1"]},
        {
            "op": "add_transit",
            "fromStopId": "stop-1",
            "toStopId": "stop-2",
            "mode": "bus",
            "departureAt": "2027-03-01T00:00:00+00:00",
            "arrivalAt": "2027-03-01T03:00:00+00:00",
            "travelers": ["traveler-1"],
            "sourceFragmentId": "tkt-01",
        },
    ]


def test_run_scenario_passes_and_prices() -> None:
    price = resolve_price("haiku")
    client = _ScriptedClient([_build_ops()])
    result = run_scenario(_passing_scenario(), client, price)
    assert result.passed is True
    assert result.failure_category is None
    assert result.fragment_latencies == [0.25]
    assert result.usage.input_tokens == 100
    # cost = (100*input + 10*output) / 1e6 at haiku rates -> positive.
    assert result.cost_usd > 0


def test_run_scenario_engine_error_marks_failed_without_raising() -> None:
    price = resolve_price("haiku")
    # add_transit referencing nonexistent stops -> EngineError on first fragment.
    client = _ScriptedClient(
        [
            [
                {
                    "op": "add_transit",
                    "fromStopId": "stop-1",
                    "toStopId": "stop-2",
                    "mode": "bus",
                    "departureAt": "2027-03-01T00:00:00+00:00",
                    "arrivalAt": "2027-03-01T03:00:00+00:00",
                    "travelers": ["traveler-1"],
                    "sourceFragmentId": "tkt-01",
                }
            ]
        ]
    )
    result = run_scenario(_passing_scenario(), client, price)
    assert result.passed is False
    assert result.error is not None
    assert result.failure_category is None


def test_run_module_imports_without_anthropic() -> None:
    # Importing the runner must not require the optional `spike` group; the only
    # `anthropic` use is the lazy import inside make_client.
    assert hasattr(run_module, "main")
    assert callable(run_module.main)
