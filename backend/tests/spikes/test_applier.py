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
# Batch-local refs: same-batch creations referenced by model-chosen handles
# --------------------------------------------------------------------------- #


def test_refs_build_new_stops_and_transits_on_empty_route() -> None:
    """First multi-leg ticket on an empty route: HEL->ROM->LIS->CDG, all new.

    Previously impossible — transits could only reference stops that already
    existed. Now each new stop carries a `ref` and transits wire those refs.
    """
    route = WorkingRoute()

    ops: list[Op] = [
        CreateStop(city="HEL", ref="n1"),
        CreateStop(city="ROM", after="n1", ref="n2"),
        CreateStop(city="LIS", after="n2", ref="n3"),
        CreateStop(city="CDG", after="n3", ref="n4"),
        AddTransit.model_validate(
            {
                "fromStopId": "n1",
                "toStopId": "n2",
                "mode": "air",
                "departureAt": "2027-03-01T00:00:00+00:00",
                "arrivalAt": "2027-03-01T03:00:00+00:00",
                "travelers": ["traveler-1"],
                "sourceFragmentId": "tkt-01",
            }
        ),
        AddTransit.model_validate(
            {
                "fromStopId": "n2",
                "toStopId": "n3",
                "mode": "train",
                "departureAt": "2027-03-02T00:00:00+00:00",
                "arrivalAt": "2027-03-02T03:00:00+00:00",
                "travelers": ["traveler-1"],
                "sourceFragmentId": "tkt-01",
            }
        ),
        AddTransit.model_validate(
            {
                "fromStopId": "n3",
                "toStopId": "n4",
                "mode": "bus",
                "departureAt": "2027-03-03T00:00:00+00:00",
                "arrivalAt": "2027-03-03T03:00:00+00:00",
                "travelers": ["traveler-1"],
                "sourceFragmentId": "tkt-01",
            }
        ),
    ]

    apply(route, ops)

    # Stops built in order with engine-minted ids.
    assert [s.city for s in route.stops] == ["HEL", "ROM", "LIS", "CDG"]
    assert route.stop_ids() == ["stop-1", "stop-2", "stop-3", "stop-4"]

    # Transits wired between the resolved (real) stop ids — refs never persisted.
    assert [(t.from_stop_id, t.to_stop_id) for t in route.transits] == [
        ("stop-1", "stop-2"),
        ("stop-2", "stop-3"),
        ("stop-3", "stop-4"),
    ]
    for t in route.transits:
        assert t.from_stop_id.startswith("stop-")
        assert t.to_stop_id.startswith("stop-")


def test_create_stop_after_references_earlier_ref_chains_in_order() -> None:
    """`create_stop.after` may point at a ref created earlier in the batch."""
    route = WorkingRoute()

    apply(
        route,
        [
            CreateStop(city="HEL", ref="a"),
            CreateStop(city="ROM", after="a", ref="b"),
            CreateStop(city="LIS", after="b", ref="c"),
        ],
    )

    assert [s.city for s in route.stops] == ["HEL", "ROM", "LIS"]
    assert route.stop_ids() == ["stop-1", "stop-2", "stop-3"]


def test_mixed_batch_existing_id_and_same_batch_ref() -> None:
    """One op list referencing an existing `stop-N` id AND a same-batch ref."""
    route = WorkingRoute()
    # Seed an existing stop.
    apply(route, [CreateStop(city="HEL")])
    assert route.stop_ids() == ["stop-1"]

    # New batch: create ROM (ref "n1") after the EXISTING stop-1, then a transit
    # from the existing stop-1 to the new ref "n1".
    apply(
        route,
        [
            CreateStop(city="ROM", after="stop-1", ref="n1"),
            AddTransit.model_validate(
                {
                    "fromStopId": "stop-1",
                    "toStopId": "n1",
                    "mode": "air",
                    "departureAt": "2027-03-01T00:00:00+00:00",
                    "arrivalAt": "2027-03-01T03:00:00+00:00",
                    "travelers": ["traveler-1"],
                    "sourceFragmentId": "tkt-01",
                }
            ),
            AddTravelers(stopId="n1", travelers=["traveler-2"]),
        ],
    )

    assert [s.city for s in route.stops] == ["HEL", "ROM"]
    assert route.stop_ids() == ["stop-1", "stop-2"]
    transit = route.transits[0]
    assert transit.from_stop_id == "stop-1"
    assert transit.to_stop_id == "stop-2"
    # add_travelers set traveler-2; projection then unions the incident
    # transit's traveler-1 onto the stop (explicit first, derived appended).
    assert route.stop_by_id("stop-2").travelers == ["traveler-2", "traveler-1"]  # type: ignore[union-attr]


def test_undeclared_ref_raises() -> None:
    """Referencing a ref that was never declared in this batch -> OpApplyError."""
    route = WorkingRoute()
    with pytest.raises(OpApplyError):
        apply(
            route,
            [
                CreateStop(city="HEL", ref="n1"),
                AddTransit.model_validate(
                    {
                        "fromStopId": "n1",
                        "toStopId": "n2",  # never declared
                        "mode": "air",
                        "departureAt": "2027-03-01T00:00:00+00:00",
                        "arrivalAt": "2027-03-01T03:00:00+00:00",
                        "travelers": ["traveler-1"],
                        "sourceFragmentId": "tkt-01",
                    }
                ),
            ],
        )


def test_forward_ref_raises() -> None:
    """Referencing a ref declared LATER in the same batch -> OpApplyError."""
    route = WorkingRoute()
    with pytest.raises(OpApplyError):
        apply(
            route,
            [
                # `after` points at "b" before its create_stop runs.
                CreateStop(city="HEL", after="b", ref="a"),
                CreateStop(city="ROM", after="a", ref="b"),
            ],
        )


def test_duplicate_ref_declaration_raises() -> None:
    """Declaring the same `ref` twice in a batch -> OpApplyError."""
    route = WorkingRoute()
    with pytest.raises(OpApplyError):
        apply(
            route,
            [
                CreateStop(city="HEL", ref="n1"),
                CreateStop(city="ROM", after="n1", ref="n1"),
            ],
        )


def test_refs_are_batch_local_not_persisted_across_apply_calls() -> None:
    """A ref from one apply() call is not visible in the next."""
    route = WorkingRoute()
    apply(route, [CreateStop(city="HEL", ref="n1")])
    # "n1" is not a real stop id and the new batch has no such ref.
    with pytest.raises(OpApplyError):
        apply(route, [AddTravelers(stopId="n1", travelers=["traveler-1"])])


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
# Stop projection: derive arrival/departure/travelers from transits
# --------------------------------------------------------------------------- #


def test_projection_derives_stop_timing_from_transits() -> None:
    """create + transit ops alone yield derived stop arrival/departure.

    First stop has no incoming transit (arrivalAt None); last stop has no
    outgoing transit (departureAt None); the middle stop carries both.
    """
    route = WorkingRoute()
    apply(
        route,
        [
            CreateStop(city="JFK", ref="n1"),
            CreateStop(city="FRA", after="n1", ref="n2"),
            CreateStop(city="LIS", after="n2", ref="n3"),
            AddTransit.model_validate(
                {
                    "fromStopId": "n1",
                    "toStopId": "n2",
                    "mode": "air",
                    "departureAt": "2027-03-01T00:00:00+00:00",
                    "arrivalAt": "2027-03-01T03:00:00+00:00",
                    "travelers": ["traveler-1"],
                    "sourceFragmentId": "tkt-01",
                }
            ),
            AddTransit.model_validate(
                {
                    "fromStopId": "n2",
                    "toStopId": "n3",
                    "mode": "air",
                    "departureAt": "2027-03-01T08:00:00+00:00",
                    "arrivalAt": "2027-03-01T11:00:00+00:00",
                    "travelers": ["traveler-1"],
                    "sourceFragmentId": "tkt-01",
                }
            ),
        ],
    )

    jfk, fra, lis = route.stops
    # First stop: only a departure (from the outgoing leg), no arrival.
    assert jfk.arrival_at is None
    assert jfk.departure_at == _dt("2027-03-01T00:00:00")
    # Middle stop: arrival from incoming leg, departure from outgoing leg.
    assert fra.arrival_at == _dt("2027-03-01T03:00:00")
    assert fra.departure_at == _dt("2027-03-01T08:00:00")
    # Last stop: only an arrival (from the incoming leg), no departure.
    assert lis.arrival_at == _dt("2027-03-01T11:00:00")
    assert lis.departure_at is None
    # Travelers projected onto every incident stop.
    assert jfk.travelers == ["traveler-1"]
    assert fra.travelers == ["traveler-1"]
    assert lis.travelers == ["traveler-1"]


def test_projection_unions_multi_traveler_from_transits() -> None:
    """A stop incident to transits with different travelers unions them."""
    route = WorkingRoute()
    apply(
        route,
        [
            CreateStop(city="HEL", ref="n1"),
            CreateStop(city="ROM", after="n1", ref="n2"),
            CreateStop(city="LIS", after="n2", ref="n3"),
            AddTransit.model_validate(
                {
                    "fromStopId": "n1",
                    "toStopId": "n2",
                    "mode": "air",
                    "departureAt": "2027-03-01T00:00:00+00:00",
                    "arrivalAt": "2027-03-01T03:00:00+00:00",
                    "travelers": ["traveler-1"],
                    "sourceFragmentId": "tkt-01",
                }
            ),
            AddTransit.model_validate(
                {
                    "fromStopId": "n2",
                    "toStopId": "n3",
                    "mode": "air",
                    "departureAt": "2027-03-01T08:00:00+00:00",
                    "arrivalAt": "2027-03-01T11:00:00+00:00",
                    "travelers": ["traveler-2", "traveler-1"],
                    "sourceFragmentId": "tkt-01",
                }
            ),
        ],
    )

    hel, rom, lis = route.stops
    assert hel.travelers == ["traveler-1"]
    # ROM is incident to BOTH legs: union, stable order, no duplicates
    # (incoming leg's traveler-1 first, then the outgoing leg's traveler-2).
    assert rom.travelers == ["traveler-1", "traveler-2"]
    assert lis.travelers == ["traveler-2", "traveler-1"]


def test_projection_keeps_explicit_timing_on_no_transit_stop() -> None:
    """A stop with NO transit keeps its explicit enrich_stop/add_travelers.

    This is the override/fallback path: nothing to derive, so the explicit
    values stand.
    """
    route = WorkingRoute()
    apply(
        route,
        [
            CreateStop(city="ROM"),
            EnrichStop.model_validate(
                {
                    "stopId": "stop-1",
                    "arrivalAt": "2027-03-01T03:00:00+00:00",
                    "departureAt": "2027-03-01T08:00:00+00:00",
                }
            ),
            AddTravelers(stopId="stop-1", travelers=["traveler-1", "traveler-2"]),
        ],
    )

    rom = route.stops[0]
    assert rom.arrival_at == _dt("2027-03-01T03:00:00")
    assert rom.departure_at == _dt("2027-03-01T08:00:00")
    assert rom.travelers == ["traveler-1", "traveler-2"]


def test_projection_is_fill_only_explicit_timing_preserved_on_transit_stop() -> None:
    """Explicit enrich_stop timing on a transit stop wins over derived (fill-only).

    The transit's own times differ from the explicit ones; projection must NOT
    overwrite the explicit values, only fill where unset.
    """
    route = WorkingRoute()
    apply(
        route,
        [
            CreateStop(city="HEL", ref="n1"),
            CreateStop(city="ROM", after="n1", ref="n2"),
            # Explicitly pin ROM's arrival to a value DIFFERENT from the leg's.
            EnrichStop.model_validate(
                {"stopId": "n2", "arrivalAt": "2027-03-01T05:00:00+00:00"}
            ),
            AddTransit.model_validate(
                {
                    "fromStopId": "n1",
                    "toStopId": "n2",
                    "mode": "air",
                    "departureAt": "2027-03-01T00:00:00+00:00",
                    "arrivalAt": "2027-03-01T03:00:00+00:00",
                    "travelers": ["traveler-1"],
                    "sourceFragmentId": "tkt-01",
                }
            ),
        ],
    )

    rom = route.stops[1]
    # Explicit arrival kept (fill-only); NOT overwritten by the leg's 03:00.
    assert rom.arrival_at == _dt("2027-03-01T05:00:00")
    # Travelers still unioned from the incident transit.
    assert rom.travelers == ["traveler-1"]


def test_projection_does_not_change_stop_identity_or_order() -> None:
    """Projection only fills display fields — ids and ordering are untouched."""
    route = WorkingRoute()
    apply(
        route,
        [
            CreateStop(city="HEL", ref="n1"),
            CreateStop(city="ROM", after="n1", ref="n2"),
            AddTransit.model_validate(
                {
                    "fromStopId": "n1",
                    "toStopId": "n2",
                    "mode": "bus",
                    "departureAt": "2027-03-01T00:00:00+00:00",
                    "arrivalAt": "2027-03-01T03:00:00+00:00",
                    "travelers": ["traveler-1"],
                    "sourceFragmentId": "tkt-01",
                }
            ),
        ],
    )

    assert route.stop_ids() == ["stop-1", "stop-2"]
    assert [s.city for s in route.stops] == ["HEL", "ROM"]


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
