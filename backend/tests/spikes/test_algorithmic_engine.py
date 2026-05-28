"""Unit tests for the Slice-1 algorithmic engine.

Slice 1 only handles a single-leg transit ticket on an empty route; everything
else (multi-leg, hotel, non-empty route) must raise
:class:`RuleNotImplementedError` from the rules layer and be wrapped as an
:class:`EngineError` by the engine. These tests cover both happy paths and
those out-of-scope guards.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from spikes.route_engine_algorithmic.engine import EngineError, update_route
from spikes.route_engine_algorithmic.rules import RuleNotImplementedError, build_ops
from spikes.route_engine_llm.models import (
    HotelBookingFragment,
    TransitMode,
    TransitTicketFragment,
    WorkingRoute,
)
from spikes.route_engine_llm.operations import AddTransit, CreateStop


def _dt(iso: str) -> datetime:
    """Build a UTC datetime for fixture readability."""
    return datetime.fromisoformat(iso).replace(tzinfo=UTC)


def _single_leg_air_ticket() -> TransitTicketFragment:
    """Hand-crafted single-leg air ticket used across the happy-path tests."""
    return TransitTicketFragment.model_validate(
        {
            "documentType": "air-ticket",
            "sourceDocumentId": "doc-alpha-01",
            "pnr": "ABC123",
            "travelers": ["traveler-1"],
            "legs": [
                {
                    "from": "JFK",
                    "to": "FRA",
                    "departureAt": "2027-03-01T00:00:00Z",
                    "arrivalAt": "2027-03-01T08:00:00Z",
                    "carrier": "LO",
                    "vehicleNumber": "LO100",
                }
            ],
        }
    )


def _multi_leg_air_ticket() -> TransitTicketFragment:
    """Out-of-Slice-1 shape: two legs in one ticket."""
    return TransitTicketFragment.model_validate(
        {
            "documentType": "air-ticket",
            "sourceDocumentId": "doc-beta-01",
            "pnr": "DEF456",
            "travelers": ["traveler-1"],
            "legs": [
                {
                    "from": "JFK",
                    "to": "FRA",
                    "departureAt": "2027-03-01T00:00:00Z",
                    "arrivalAt": "2027-03-01T08:00:00Z",
                },
                {
                    "from": "FRA",
                    "to": "LIS",
                    "departureAt": "2027-03-01T10:00:00Z",
                    "arrivalAt": "2027-03-01T13:00:00Z",
                },
            ],
        }
    )


def _hotel_booking() -> HotelBookingFragment:
    """Out-of-Slice-1 shape: a hotel booking."""
    return HotelBookingFragment.model_validate(
        {
            "documentType": "hotel-booking",
            "sourceDocumentId": "doc-gamma-01",
            "confirmationCode": "HOTEL-1",
            "travelers": ["traveler-1"],
            "city": "LIS",
            "checkInAt": "2027-03-01T15:00:00Z",
            "checkOutAt": "2027-03-03T11:00:00Z",
            "hotelName": "Hotel Lisboa",
        }
    )


# --------------------------------------------------------------------------- #
# rules.build_ops — happy path
# --------------------------------------------------------------------------- #


def test_build_ops_emits_three_ops_for_single_leg_on_empty_route() -> None:
    """The supported shape produces exactly create+create+transit, in order."""
    route = WorkingRoute()
    fragment = _single_leg_air_ticket()

    ops = build_ops(route, fragment)

    assert len(ops) == 3
    assert isinstance(ops[0], CreateStop)
    assert ops[0].city == "JFK"
    assert ops[0].after is None
    assert ops[0].ref == "n1"

    assert isinstance(ops[1], CreateStop)
    assert ops[1].city == "FRA"
    assert ops[1].after == "n1"
    assert ops[1].ref == "n2"

    assert isinstance(ops[2], AddTransit)
    transit = ops[2]
    assert transit.from_stop_id == "n1"
    assert transit.to_stop_id == "n2"
    assert transit.mode == TransitMode.AIR
    assert transit.departure_at == _dt("2027-03-01T00:00:00Z")
    assert transit.arrival_at == _dt("2027-03-01T08:00:00Z")
    assert transit.travelers == ["traveler-1"]
    assert transit.source_fragment_id == "doc-alpha-01"


# --------------------------------------------------------------------------- #
# rules.build_ops — out-of-scope guards
# --------------------------------------------------------------------------- #


def test_build_ops_rejects_multi_leg_ticket() -> None:
    """A multi-leg ticket is out of Slice-1 scope and must raise the marker error."""
    route = WorkingRoute()
    with pytest.raises(RuleNotImplementedError, match="single-leg"):
        build_ops(route, _multi_leg_air_ticket())


def test_build_ops_rejects_hotel_fragment() -> None:
    """Hotel bookings are out of Slice-1 scope."""
    route = WorkingRoute()
    with pytest.raises(RuleNotImplementedError, match="hotel"):
        build_ops(route, _hotel_booking())


def test_build_ops_rejects_non_empty_route() -> None:
    """Slice 1 only handles the first fragment of a scenario."""
    route = WorkingRoute()
    fragment = _single_leg_air_ticket()
    # Seed the route by applying the first fragment, then attempt a second one.
    update_route(route, fragment)

    with pytest.raises(RuleNotImplementedError, match="empty route"):
        build_ops(route, _single_leg_air_ticket())


# --------------------------------------------------------------------------- #
# engine.update_route — end-to-end
# --------------------------------------------------------------------------- #


def test_update_route_single_leg_builds_expected_route_shape() -> None:
    """End-to-end: empty route + single-leg ticket yields two stops + one transit."""
    route = WorkingRoute()
    fragment = _single_leg_air_ticket()

    result = update_route(route, fragment)

    # Same instance is returned, mutated in place.
    assert result.route is route

    # Stops: ordered by leg direction, with projected timings from the transit.
    assert [s.city for s in route.stops] == ["JFK", "FRA"]
    assert route.stops[0].departure_at == _dt("2027-03-01T00:00:00Z")
    assert route.stops[1].arrival_at == _dt("2027-03-01T08:00:00Z")
    assert route.stops[0].travelers == ["traveler-1"]
    assert route.stops[1].travelers == ["traveler-1"]

    # Exactly one transit, wired between the engine-minted stop ids.
    assert len(route.transits) == 1
    transit = route.transits[0]
    assert transit.from_stop_id == route.stops[0].id
    assert transit.to_stop_id == route.stops[1].id
    assert transit.mode == TransitMode.AIR
    assert transit.source_fragment_id == "doc-alpha-01"

    # Algorithmic has no token spend; latency is real wall-clock.
    assert result.usage.input_tokens == 0
    assert result.usage.output_tokens == 0
    assert result.latency_seconds > 0


def test_update_route_wraps_rule_not_implemented_as_engine_error() -> None:
    """Out-of-scope shapes surface as an EngineError chained from the marker."""
    route = WorkingRoute()
    with pytest.raises(EngineError) as exc_info:
        update_route(route, _multi_leg_air_ticket())

    assert isinstance(exc_info.value.__cause__, RuleNotImplementedError)
    # Route was not mutated — no ops applied.
    assert route.stops == []
    assert route.transits == []
