"""Contract test for the engine entrypoint, driven by a FAKE Bedrock client.

No network, no AWS credentials, and crucially NO ``anthropic`` import — the
engine depends only on the :class:`BedrockEngineClient` protocol, which we
satisfy with a stub that returns a hand-crafted tool-use op list. This proves
``update_route`` end-to-end (prompt render -> tool input parse -> applier) while
staying CI-safe.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from spikes.route_engine_llm.bedrock_client import ToolUseResult, Usage
from spikes.route_engine_llm.engine import EngineError, update_route
from spikes.route_engine_llm.models import (
    TransitMode,
    TransitTicketFragment,
    WorkingRoute,
)


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=UTC)


class FakeBedrockClient:
    """A stub :class:`BedrockEngineClient` returning a fixed tool input.

    Records the last call's arguments so tests can assert the engine renders the
    prompt and forces tool-use correctly.
    """

    def __init__(self, operations: list[dict[str, Any]]) -> None:
        self._operations = operations
        self.last_call: dict[str, Any] | None = None

    def complete(
        self,
        *,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any],
    ) -> ToolUseResult:
        self.last_call = {
            "system": system,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
        }
        return ToolUseResult(
            tool_input={"operations": self._operations},
            usage=Usage(
                input_tokens=1200, output_tokens=80, cache_read_input_tokens=900
            ),
            latency_seconds=0.5,
        )


def _bus_fragment() -> TransitTicketFragment:
    return TransitTicketFragment.model_validate(
        {
            "documentType": "bus-ticket",
            "sourceDocumentId": "tkt-01",
            "pnr": "ABC123",
            "travelers": ["traveler-1"],
            "legs": [
                {
                    "from": "HEL",
                    "to": "ROM",
                    "departureAt": "2027-03-01T00:00:00+00:00",
                    "arrivalAt": "2027-03-01T03:00:00+00:00",
                }
            ],
        }
    )


# --------------------------------------------------------------------------- #
# Happy path: a transit ticket builds two stops + one transit
# --------------------------------------------------------------------------- #


def test_update_route_builds_stops_and_transit_from_transit_fragment() -> None:
    route = WorkingRoute()
    fragment = _bus_fragment()

    # Hand-crafted op list a model would return for a HEL->ROM bus leg into an
    # empty route: create both endpoints in order, enrich timing, add the leg.
    client = FakeBedrockClient(
        [
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
    )

    result = update_route(route, fragment, client)

    # The route mutated in place and now holds the two ordered stops + transit.
    assert result.route is route
    assert [s.city for s in route.stops] == ["HEL", "ROM"]
    assert route.stop_ids() == ["stop-1", "stop-2"]

    hel, rom = route.stops
    assert hel.departure_at is not None
    assert rom.arrival_at is not None

    assert len(route.transits) == 1
    transit = route.transits[0]
    assert transit.from_stop_id == "stop-1"
    assert transit.to_stop_id == "stop-2"
    assert transit.mode is TransitMode.BUS
    assert transit.source_fragment_id == "tkt-01"
    assert transit.travelers == ["traveler-1"]

    # Usage + latency are surfaced for the runner to aggregate.
    assert result.usage.input_tokens == 1200
    assert result.usage.cache_read_input_tokens == 900
    assert result.latency_seconds == pytest.approx(0.5)


def test_update_route_builds_multi_leg_route_via_refs() -> None:
    """All-new multi-leg ticket on an empty route, wired with same-batch refs.

    This is the pattern that was previously impossible end-to-end: new stops
    referenced by transits within one response. The model gives each new stop a
    `ref` and the applier resolves the refs to minted ids.
    """
    route = WorkingRoute()
    fragment = _bus_fragment()

    client = FakeBedrockClient(
        [
            {"op": "create_stop", "city": "HEL", "ref": "n1"},
            {"op": "create_stop", "city": "ROM", "after": "n1", "ref": "n2"},
            {
                "op": "add_transit",
                "fromStopId": "n1",
                "toStopId": "n2",
                "mode": "bus",
                "departureAt": "2027-03-01T00:00:00+00:00",
                "arrivalAt": "2027-03-01T03:00:00+00:00",
                "travelers": ["traveler-1"],
                "sourceFragmentId": "tkt-01",
            },
        ]
    )

    update_route(route, fragment, client)

    assert [s.city for s in route.stops] == ["HEL", "ROM"]
    assert route.stop_ids() == ["stop-1", "stop-2"]
    transit = route.transits[0]
    assert transit.from_stop_id == "stop-1"
    assert transit.to_stop_id == "stop-2"
    assert transit.mode is TransitMode.BUS

    # The engine derives stop timing + travelers from the transit (the op list
    # carries NO enrich_stop / add_travelers — exactly the new prompt's flow).
    hel, rom = route.stops
    assert hel.arrival_at is None
    assert hel.departure_at == _dt("2027-03-01T00:00:00")
    assert rom.arrival_at == _dt("2027-03-01T03:00:00")
    assert rom.departure_at is None
    assert hel.travelers == ["traveler-1"]
    assert rom.travelers == ["traveler-1"]


def test_update_route_forces_tool_use_and_renders_route_and_fragment() -> None:
    route = WorkingRoute()
    fragment = _bus_fragment()
    client = FakeBedrockClient([])

    update_route(route, fragment, client)

    assert client.last_call is not None
    # Forced tool-use on the single operations tool.
    tool_choice = client.last_call["tool_choice"]
    assert tool_choice["type"] == "tool"
    tools = client.last_call["tools"]
    assert len(tools) == 1
    assert tool_choice["name"] == tools[0]["name"]
    # The user message carries both the current route and the new fragment.
    user_text = client.last_call["messages"][0]["content"]
    assert "CURRENT ROUTE" in user_text
    assert "tkt-01" in user_text
    # System prompt + tool schema are cached (prompt caching markers present).
    assert client.last_call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert tools[0]["cache_control"] == {"type": "ephemeral"}


# --------------------------------------------------------------------------- #
# Typed failures: dangling reference and malformed payload
# --------------------------------------------------------------------------- #


def test_update_route_surfaces_dangling_op_as_engine_error() -> None:
    route = WorkingRoute()
    fragment = _bus_fragment()
    # add_transit references stop ids that do not exist in the empty route.
    client = FakeBedrockClient(
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
    )

    with pytest.raises(EngineError):
        update_route(route, fragment, client)


def test_update_route_surfaces_invalid_payload_as_engine_error() -> None:
    route = WorkingRoute()
    fragment = _bus_fragment()
    # Unknown op discriminator -> pydantic validation failure.
    client = FakeBedrockClient([{"op": "teleport", "city": "ROM"}])

    with pytest.raises(EngineError):
        update_route(route, fragment, client)
