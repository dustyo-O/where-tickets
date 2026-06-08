"""Tests for `_legs_from_stations` — compact-stations → chronological legs.

DUS-31 Slice 3 replaced the public ``Fragment.legs`` model with a compact
``stations[]`` list. The algorithmic rules derive legs internally via
:func:`spikes.route_engine_algorithmic.rules._legs_from_stations`, asserting
strict ``departure → arrival → departure → ...`` alternation across the
flattened edge sequence.

These tests cover the documented sub-task 8 cases:

- 1 station (error)
- 2 stations straight
- 3 stations layover
- 3 stations return
- 4 stations dual-direction
- malformed alternation (two arrivals in a row)
- station propagation onto each ``_LegView``

Plus a tiny ``final_route_match`` insensitivity check covering the new working
route fields (``RouteStop.stations``, ``Transit.originStation`` /
``destinationStation``) so the engine can grow station detail without
breaking comparison against an expected-route that lacks those fields.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from spikes.route_engine_algorithmic.rules import (
    RuleNotImplementedError,
    _legs_from_stations,
)
from spikes.route_engine_llm.corpus import ExpectedRoute
from spikes.route_engine_llm.models import (
    RouteStop,
    Station,
    Transit,
    TransitMode,
    WorkingRoute,
)
from spikes.route_engine_llm.scoring import final_route_match


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=UTC)


def _station(
    city: str,
    *,
    departure: str | None = None,
    arrival: str | None = None,
    identifier: str | None = None,
) -> Station:
    return Station.model_validate(
        {
            "city": city,
            "kind": "airport",
            "identifier": identifier or city,
            **({"departureAt": departure} if departure is not None else {}),
            **({"arrivalAt": arrival} if arrival is not None else {}),
        }
    )


# --------------------------------------------------------------------------- #
# _legs_from_stations — happy paths across station counts
# --------------------------------------------------------------------------- #


def test_one_station_raises_rule_not_implemented() -> None:
    """A single departure-only station has nothing to pair into a leg."""
    station = _station("A", departure="2027-03-01T00:00:00Z")
    with pytest.raises(RuleNotImplementedError, match="odd number"):
        _legs_from_stations([station])


def test_two_stations_straight_yields_one_leg() -> None:
    """Origin (dep only) + terminus (arr only) → one A→B leg."""
    a = _station("A", departure="2027-03-01T00:00:00Z")
    b = _station("B", arrival="2027-03-01T03:00:00Z")
    legs = _legs_from_stations([a, b])

    assert len(legs) == 1
    (leg,) = legs
    assert leg.from_city == "A"
    assert leg.to_city == "B"
    assert leg.departure_at == _dt("2027-03-01T00:00:00Z")
    assert leg.arrival_at == _dt("2027-03-01T03:00:00Z")


def test_three_stations_layover_yields_two_legs() -> None:
    """A dep → B arr+dep → C arr → legs A→B, B→C."""
    a = _station("A", departure="2027-03-01T00:00:00Z")
    b = _station(
        "B",
        arrival="2027-03-01T03:00:00Z",
        departure="2027-03-01T05:00:00Z",
    )
    c = _station("C", arrival="2027-03-01T08:00:00Z")
    legs = _legs_from_stations([a, b, c])

    assert [(leg.from_city, leg.to_city) for leg in legs] == [("A", "B"), ("B", "C")]
    assert legs[0].arrival_at == _dt("2027-03-01T03:00:00Z")
    assert legs[1].departure_at == _dt("2027-03-01T05:00:00Z")


def test_three_stations_return_yields_two_legs_back_to_origin() -> None:
    """A dep → B arr+dep → A arr → legs A→B, B→A; the two A entries are distinct."""
    a_out = _station("A", departure="2027-03-01T00:00:00Z", identifier="A-out")
    b = _station(
        "B",
        arrival="2027-03-01T03:00:00Z",
        departure="2027-03-05T08:00:00Z",
        identifier="B",
    )
    a_back = _station("A", arrival="2027-03-05T11:00:00Z", identifier="A-back")
    legs = _legs_from_stations([a_out, b, a_back])

    assert [(leg.from_city, leg.to_city) for leg in legs] == [("A", "B"), ("B", "A")]
    # Each leg's from/to_station point at the originating entry.
    assert legs[0].from_station is a_out
    assert legs[0].to_station is b
    assert legs[1].from_station is b
    assert legs[1].to_station is a_back


def test_four_stations_dual_direction_yields_three_legs() -> None:
    """A dep → B arr+dep → C arr+dep → A arr → legs A→B, B→C, C→A.

    Verifies the algorithm doesn't special-case "two legs only" on a return
    pattern — a layover-then-return with two transitions in the middle still
    pairs correctly.
    """
    a_out = _station("A", departure="2027-03-01T00:00:00Z")
    b = _station(
        "B",
        arrival="2027-03-01T03:00:00Z",
        departure="2027-03-02T08:00:00Z",
    )
    c = _station(
        "C",
        arrival="2027-03-02T11:00:00Z",
        departure="2027-03-05T08:00:00Z",
    )
    a_back = _station("A", arrival="2027-03-05T11:00:00Z")
    legs = _legs_from_stations([a_out, b, c, a_back])

    assert [(leg.from_city, leg.to_city) for leg in legs] == [
        ("A", "B"),
        ("B", "C"),
        ("C", "A"),
    ]
    assert legs[1].departure_at == _dt("2027-03-02T08:00:00Z")
    assert legs[1].arrival_at == _dt("2027-03-02T11:00:00Z")


# --------------------------------------------------------------------------- #
# Malformed inputs raise RuleNotImplementedError
# --------------------------------------------------------------------------- #


def test_two_consecutive_arrivals_raises_rule_not_implemented() -> None:
    """Two arrivals in a row break the strict departure→arrival alternation."""
    a = _station("A", arrival="2027-03-01T00:00:00Z")
    b = _station("B", arrival="2027-03-01T03:00:00Z")
    with pytest.raises(RuleNotImplementedError, match="alternate"):
        _legs_from_stations([a, b])


def test_empty_stations_raises_rule_not_implemented() -> None:
    """Zero stations means zero edges — nothing to pair."""
    with pytest.raises(RuleNotImplementedError, match="no station datetimes"):
        _legs_from_stations([])


# --------------------------------------------------------------------------- #
# Station propagation: from_station / to_station refer to the source entries
# --------------------------------------------------------------------------- #


def test_each_leg_carries_its_origin_and_destination_station_objects() -> None:
    """``_LegView.from_station`` / ``to_station`` are the Station objects the
    departure / arrival edges came from — same identity, not copies."""
    a = _station("A", departure="2027-03-01T00:00:00Z", identifier="A-id")
    b = _station(
        "B",
        arrival="2027-03-01T03:00:00Z",
        departure="2027-03-01T05:00:00Z",
        identifier="B-id",
    )
    c = _station("C", arrival="2027-03-01T08:00:00Z", identifier="C-id")
    legs = _legs_from_stations([a, b, c])

    assert legs[0].from_station is a
    assert legs[0].to_station is b
    assert legs[1].from_station is b
    assert legs[1].to_station is c


# --------------------------------------------------------------------------- #
# final_route_match insensitivity to new working-route station fields
# --------------------------------------------------------------------------- #


def test_final_route_match_ignores_stop_stations_and_transit_stations() -> None:
    """The expected-route schema doesn't carry stations in Slice 3, but the
    working route does. final_route_match must compare only the fields the
    expected-route knows about, so a route with stations attached still
    matches an expected route that lacks them.
    """
    expected = ExpectedRoute.model_validate(
        {
            "travelers": ["traveler-1"],
            "stops": [
                {
                    "city": "A",
                    "departureAt": "2027-03-01T00:00:00+00:00",
                    "travelers": ["traveler-1"],
                },
                {
                    "city": "B",
                    "arrivalAt": "2027-03-01T03:00:00+00:00",
                    "travelers": ["traveler-1"],
                },
            ],
            "transits": [
                {
                    "from": "A",
                    "to": "B",
                    "mode": "air",
                    "departureAt": "2027-03-01T00:00:00+00:00",
                    "arrivalAt": "2027-03-01T03:00:00+00:00",
                    "travelers": ["traveler-1"],
                    "sourceFragmentId": "tkt-01",
                }
            ],
        }
    )

    working = WorkingRoute()
    a = _station("A", departure="2027-03-01T00:00:00Z")
    b = _station("B", arrival="2027-03-01T03:00:00Z")
    # Build stops carrying stations[] — final_route_match must ignore these
    # so the comparison against an expected-route that lacks them still passes.
    working.stops.append(
        RouteStop(
            id=working.mint_stop_id(),
            city="A",
            departureAt=_dt("2027-03-01T00:00:00Z"),
            travelers=["traveler-1"],
            stations=[a],
        )
    )
    working.stops.append(
        RouteStop(
            id=working.mint_stop_id(),
            city="B",
            arrivalAt=_dt("2027-03-01T03:00:00Z"),
            travelers=["traveler-1"],
            stations=[b],
        )
    )
    working.transits.append(
        Transit(
            id=working.mint_transit_id(),
            fromStopId="stop-1",
            toStopId="stop-2",
            mode=TransitMode.AIR,
            departureAt=_dt("2027-03-01T00:00:00Z"),
            arrivalAt=_dt("2027-03-01T03:00:00Z"),
            travelers=["traveler-1"],
            sourceFragmentId="tkt-01",
            originStation=a,
            destinationStation=b,
        )
    )

    result = final_route_match(working, expected)
    assert result.passed, (
        f"final_route_match must ignore stations / originStation / "
        f"destinationStation, got failure: {result.reason!r}"
    )
    assert result.category is None
