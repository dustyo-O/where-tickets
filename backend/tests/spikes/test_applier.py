"""Offline unit tests for the deterministic operation applier (Slice 1).

No network, no DB, no AWS — pure in-memory pydantic + the applier.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from spikes.route_engine_llm.models import TransitMode, WorkingRoute
from spikes.route_engine_llm.operations import (
    AddTransit,
    AddTravelers,
    AttachAccommodation,
    CreateStop,
    EnrichStop,
    Op,
    OpApplyError,
    apply,
)


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Happy paths
# --------------------------------------------------------------------------- #


def test_create_enrich_transit_accommodation_happy_path() -> None:
    route = WorkingRoute()

    ops: list[Op] = [
        CreateStop(city="HEL"),
        CreateStop(city="ROM", after="stop-1"),
        EnrichStop.model_validate(
            {"stopId": "stop-1", "departureAt": "2027-03-01T00:00:00+00:00"}
        ),
        EnrichStop.model_validate(
            {
                "stopId": "stop-2",
                "arrivalAt": "2027-03-01T03:00:00+00:00",
                "departureAt": "2027-03-01T08:00:00+00:00",
            }
        ),
        AddTransit.model_validate(
            {
                "fromStopId": "stop-1",
                "toStopId": "stop-2",
                "mode": "bus",
                "departureAt": "2027-03-01T00:00:00+00:00",
                "arrivalAt": "2027-03-01T03:00:00+00:00",
                "travelers": ["traveler-1"],
                "sourceFragmentId": "tkt-01",
            }
        ),
        AttachAccommodation.model_validate(
            {
                "stopId": "stop-2",
                "checkInAt": "2027-03-01T03:00:00+00:00",
                "checkOutAt": "2027-03-01T08:00:00+00:00",
                "hotelName": "Hotel Roma",
            }
        ),
    ]

    apply(route, ops)

    assert route.stop_ids() == ["stop-1", "stop-2"]
    hel, rom = route.stops
    assert hel.city == "HEL"
    assert hel.departure_at == _dt("2027-03-01T00:00:00")
    assert rom.city == "ROM"
    assert rom.arrival_at == _dt("2027-03-01T03:00:00")
    assert rom.departure_at == _dt("2027-03-01T08:00:00")

    assert len(route.transits) == 1
    transit = route.transits[0]
    assert transit.id == "transit-1"
    assert transit.from_stop_id == "stop-1"
    assert transit.to_stop_id == "stop-2"
    assert transit.mode is TransitMode.BUS
    assert transit.source_fragment_id == "tkt-01"

    assert len(rom.accommodations) == 1
    assert rom.accommodations[0].hotel_name == "Hotel Roma"


def test_create_stop_prepends_at_front_when_after_is_start() -> None:
    route = WorkingRoute()
    apply(route, [CreateStop(city="ROM")])
    # `after=None` and `after="start"` both prepend at the front.
    apply(route, [CreateStop(city="HEL", after="start")])
    apply(route, [CreateStop(city="LIS", after=None)])

    assert route.stop_ids() == ["stop-3", "stop-2", "stop-1"]
    assert [s.city for s in route.stops] == ["LIS", "HEL", "ROM"]


def test_create_stop_splices_in_the_middle() -> None:
    route = WorkingRoute()
    apply(route, [CreateStop(city="HEL"), CreateStop(city="LIS", after="stop-1")])
    # Insert ROM between HEL (stop-1) and LIS (stop-2).
    apply(route, [CreateStop(city="ROM", after="stop-1")])

    assert [s.city for s in route.stops] == ["HEL", "ROM", "LIS"]
    assert route.stop_ids() == ["stop-1", "stop-3", "stop-2"]


# --------------------------------------------------------------------------- #
# Circle: double-ROM stays two distinct stops (064-circle-1p-forward)
# --------------------------------------------------------------------------- #


def test_circle_double_rom_are_separate_stops() -> None:
    """HEL -> ROM -> LIS -> CDG -> ROM: the two ROM stops never merge."""
    route = WorkingRoute()
    apply(
        route,
        [
            CreateStop(city="HEL"),
            CreateStop(city="ROM", after="stop-1"),
            CreateStop(city="LIS", after="stop-2"),
            CreateStop(city="CDG", after="stop-3"),
            CreateStop(city="ROM", after="stop-4"),
        ],
    )

    assert [s.city for s in route.stops] == ["HEL", "ROM", "LIS", "CDG", "ROM"]
    rom_stops = [s for s in route.stops if s.city == "ROM"]
    assert len(rom_stops) == 2
    first_rom, second_rom = rom_stops
    assert first_rom.id == "stop-2"
    assert second_rom.id == "stop-5"
    assert first_rom.id != second_rom.id

    # Enriching the first ROM must not touch the second ROM.
    apply(
        route,
        [
            EnrichStop.model_validate(
                {
                    "stopId": "stop-2",
                    "arrivalAt": "2027-03-01T03:00:00+00:00",
                    "departureAt": "2027-03-01T08:00:00+00:00",
                }
            ),
        ],
    )
    apply(
        route,
        [
            EnrichStop.model_validate(
                {"stopId": "stop-5", "arrivalAt": "2027-03-02T03:00:00+00:00"}
            ),
        ],
    )

    assert first_rom.arrival_at == _dt("2027-03-01T03:00:00")
    assert first_rom.departure_at == _dt("2027-03-01T08:00:00")
    assert second_rom.arrival_at == _dt("2027-03-02T03:00:00")
    # The first ROM departs; the final ROM is the trip's end (no departure).
    assert second_rom.departure_at is None


# --------------------------------------------------------------------------- #
# Multi-pax traveler union
# --------------------------------------------------------------------------- #


def test_add_travelers_unions_without_duplicates_stable_order() -> None:
    route = WorkingRoute()
    apply(route, [CreateStop(city="HEL")])
    apply(
        route, [AddTravelers(stopId="stop-1", travelers=["traveler-1", "traveler-2"])]
    )
    apply(
        route, [AddTravelers(stopId="stop-1", travelers=["traveler-2", "traveler-3"])]
    )

    assert route.stops[0].travelers == ["traveler-1", "traveler-2", "traveler-3"]


# --------------------------------------------------------------------------- #
# Dangling-id rejection (every op type)
# --------------------------------------------------------------------------- #


def test_create_stop_rejects_unknown_after_id() -> None:
    route = WorkingRoute()
    with pytest.raises(OpApplyError):
        apply(route, [CreateStop(city="ROM", after="stop-99")])


def test_enrich_stop_rejects_unknown_id() -> None:
    route = WorkingRoute()
    with pytest.raises(OpApplyError):
        apply(
            route,
            [EnrichStop.model_validate({"stopId": "stop-99", "arrivalAt": None})],
        )


def test_add_transit_rejects_unknown_from_id() -> None:
    route = WorkingRoute()
    apply(route, [CreateStop(city="HEL")])
    with pytest.raises(OpApplyError):
        apply(
            route,
            [
                AddTransit.model_validate(
                    {
                        "fromStopId": "stop-99",
                        "toStopId": "stop-1",
                        "mode": "air",
                        "departureAt": "2027-03-01T00:00:00+00:00",
                        "arrivalAt": "2027-03-01T03:00:00+00:00",
                        "travelers": ["traveler-1"],
                        "sourceFragmentId": "tkt-01",
                    }
                )
            ],
        )


def test_add_transit_rejects_unknown_to_id() -> None:
    route = WorkingRoute()
    apply(route, [CreateStop(city="HEL")])
    with pytest.raises(OpApplyError):
        apply(
            route,
            [
                AddTransit.model_validate(
                    {
                        "fromStopId": "stop-1",
                        "toStopId": "stop-99",
                        "mode": "air",
                        "departureAt": "2027-03-01T00:00:00+00:00",
                        "arrivalAt": "2027-03-01T03:00:00+00:00",
                        "travelers": ["traveler-1"],
                        "sourceFragmentId": "tkt-01",
                    }
                )
            ],
        )


def test_attach_accommodation_rejects_unknown_id() -> None:
    route = WorkingRoute()
    with pytest.raises(OpApplyError):
        apply(
            route,
            [
                AttachAccommodation.model_validate(
                    {
                        "stopId": "stop-99",
                        "checkInAt": "2027-03-01T03:00:00+00:00",
                        "checkOutAt": "2027-03-01T08:00:00+00:00",
                    }
                )
            ],
        )


def test_add_travelers_rejects_unknown_id() -> None:
    route = WorkingRoute()
    with pytest.raises(OpApplyError):
        apply(route, [AddTravelers(stopId="stop-99", travelers=["traveler-1"])])


# --------------------------------------------------------------------------- #
# Conflict rejection
# --------------------------------------------------------------------------- #


def test_enrich_stop_conflicting_value_raises() -> None:
    route = WorkingRoute()
    apply(route, [CreateStop(city="ROM")])
    apply(
        route,
        [
            EnrichStop.model_validate(
                {"stopId": "stop-1", "arrivalAt": "2027-03-01T03:00:00+00:00"}
            )
        ],
    )

    with pytest.raises(OpApplyError):
        apply(
            route,
            [
                EnrichStop.model_validate(
                    {"stopId": "stop-1", "arrivalAt": "2027-03-01T09:00:00+00:00"}
                )
            ],
        )


def test_enrich_stop_same_value_is_noop() -> None:
    route = WorkingRoute()
    apply(route, [CreateStop(city="ROM")])
    enrich = EnrichStop.model_validate(
        {"stopId": "stop-1", "arrivalAt": "2027-03-01T03:00:00+00:00"}
    )
    apply(route, [enrich])
    # Re-applying the identical value must not raise.
    apply(route, [enrich])

    assert route.stops[0].arrival_at == _dt("2027-03-01T03:00:00")


# --------------------------------------------------------------------------- #
# Invariant: applying ops never reassigns an existing id
# --------------------------------------------------------------------------- #


def test_applying_ops_never_reassigns_existing_ids() -> None:
    route = WorkingRoute()
    apply(route, [CreateStop(city="HEL"), CreateStop(city="ROM", after="stop-1")])
    ids_before = route.stop_ids()
    transit_seq_before = route.next_transit_seq

    apply(
        route,
        [
            # New stop spliced in the middle...
            CreateStop(city="LIS", after="stop-1"),
            # ...and a transit added: existing IDs must be untouched.
            AddTransit.model_validate(
                {
                    "fromStopId": "stop-1",
                    "toStopId": "stop-2",
                    "mode": "train",
                    "departureAt": "2027-03-01T00:00:00+00:00",
                    "arrivalAt": "2027-03-01T03:00:00+00:00",
                    "travelers": ["traveler-1"],
                    "sourceFragmentId": "tkt-01",
                }
            ),
            EnrichStop.model_validate(
                {"stopId": "stop-2", "arrivalAt": "2027-03-01T03:00:00+00:00"}
            ),
            AddTravelers(stopId="stop-1", travelers=["traveler-2"]),
        ],
    )

    # Every previously-existing id is still present and maps to the same city.
    assert set(ids_before).issubset(set(route.stop_ids()))
    assert route.stop_by_id("stop-1").city == "HEL"  # type: ignore[union-attr]
    assert route.stop_by_id("stop-2").city == "ROM"  # type: ignore[union-attr]
    # The new stop got a fresh, never-before-used id.
    assert "stop-3" in route.stop_ids()
    # Transit counter advanced monotonically (never rewound / reused).
    assert route.next_transit_seq == transit_seq_before + 1
