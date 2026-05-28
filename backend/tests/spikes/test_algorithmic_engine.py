"""Unit tests for the algorithmic engine.

Slice 1 covered: single-leg transit on an empty route, with hard guards on
out-of-scope shapes.

Slice 2 extends to: multi-leg transit tickets and chronological insertion
against a non-empty route (front / middle / end). Hotel-booking fragments
remain out of scope and must raise :class:`RuleNotImplementedError` → wrapped
to :class:`EngineError`.

Slice 3 adds the per-traveler-per-slot identity classifier (CREATE-vs-ENRICH)
driven by the three conditions from the LLM prompt plus the
arrival-after-departure sanity check. The new tests mirror the prompt's
worked examples: forward-circle revisit (condition b), reverse-ordering
earlier-event revisit (condition b in the earlier direction), straight-return
(the wedge that broke Slice 2's trivial reuse), per-traveler slot conflict
(condition c), and the sanity check.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from spikes.route_engine_algorithmic.engine import EngineError, update_route
from spikes.route_engine_algorithmic.rules import (
    RuleNotImplementedError,
    build_ops,
    find_after_neighbor,
)
from spikes.route_engine_llm.models import (
    HotelBookingFragment,
    RouteStop,
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


def _three_leg_air_ticket() -> TransitTicketFragment:
    """Hand-crafted three-leg air ticket: JFK -> FRA -> LIS -> MAD."""
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
                {
                    "from": "LIS",
                    "to": "MAD",
                    "departureAt": "2027-03-01T15:00:00Z",
                    "arrivalAt": "2027-03-01T17:00:00Z",
                },
            ],
        }
    )


def _ticket_one_leg(
    *,
    source_id: str,
    from_city: str,
    to_city: str,
    departure: str,
    arrival: str,
) -> TransitTicketFragment:
    """Tiny factory for crafting single-leg tickets in chronological-insert tests."""
    return TransitTicketFragment.model_validate(
        {
            "documentType": "air-ticket",
            "sourceDocumentId": source_id,
            "pnr": "PNR" + source_id[-3:],
            "travelers": ["traveler-1"],
            "legs": [
                {
                    "from": from_city,
                    "to": to_city,
                    "departureAt": departure,
                    "arrivalAt": arrival,
                }
            ],
        }
    )


def _hotel_booking() -> HotelBookingFragment:
    """Out-of-Slice-2 shape: a hotel booking (Slice 4)."""
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
# rules.build_ops — single-leg on empty route (Slice 1 regression)
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
# rules.build_ops — multi-leg on empty route
# --------------------------------------------------------------------------- #


def test_build_ops_multi_leg_chains_refs_via_after() -> None:
    """A 3-leg ticket on an empty route emits 4 create_stops + 3 transits.

    The shared city between consecutive legs (FRA between legs 1 and 2; LIS
    between legs 2 and 3) reuses the same ref — never re-created.
    """
    route = WorkingRoute()
    fragment = _three_leg_air_ticket()

    ops = build_ops(route, fragment)

    creates = [o for o in ops if isinstance(o, CreateStop)]
    transits = [o for o in ops if isinstance(o, AddTransit)]
    assert len(creates) == 4
    assert len(transits) == 3

    assert [c.city for c in creates] == ["JFK", "FRA", "LIS", "MAD"]
    assert [c.ref for c in creates] == ["n1", "n2", "n3", "n4"]
    # Chained: n1 prepended (after=None), then each subsequent after the prior ref.
    assert [c.after for c in creates] == [None, "n1", "n2", "n3"]

    assert [(t.from_stop_id, t.to_stop_id) for t in transits] == [
        ("n1", "n2"),
        ("n2", "n3"),
        ("n3", "n4"),
    ]


def test_update_route_multi_leg_builds_4_stops_3_transits() -> None:
    """End-to-end multi-leg: 4 minted stops in fragment order, 3 transits wired."""
    route = WorkingRoute()
    fragment = _three_leg_air_ticket()

    update_route(route, fragment)

    assert [s.city for s in route.stops] == ["JFK", "FRA", "LIS", "MAD"]
    assert len(route.transits) == 3
    # Stops carry projected timings from their incident transits.
    assert route.stops[0].departure_at == _dt("2027-03-01T00:00:00Z")
    assert route.stops[-1].arrival_at == _dt("2027-03-01T17:00:00Z")


# --------------------------------------------------------------------------- #
# rules.build_ops — multi-leg with one endpoint already in the route
# --------------------------------------------------------------------------- #


def test_build_ops_reuses_existing_same_city_stop() -> None:
    """A ticket whose ``from`` city is already in the route references its id."""
    # Seed: JFK -> FRA already in the route.
    route = WorkingRoute()
    update_route(route, _single_leg_air_ticket())
    fra_id = next(s.id for s in route.stops if s.city == "FRA")

    # New ticket departing FRA -> LIS — FRA must be reused, not re-created.
    fragment = _ticket_one_leg(
        source_id="doc-second-01",
        from_city="FRA",
        to_city="LIS",
        departure="2027-03-02T08:00:00Z",
        arrival="2027-03-02T11:00:00Z",
    )

    ops = build_ops(route, fragment)
    creates = [o for o in ops if isinstance(o, CreateStop)]
    transits = [o for o in ops if isinstance(o, AddTransit)]
    # Only LIS is new.
    assert [c.city for c in creates] == ["LIS"]
    assert transits[0].from_stop_id == fra_id
    assert transits[0].to_stop_id == creates[0].ref


# --------------------------------------------------------------------------- #
# Chronological insertion against a non-empty route
# --------------------------------------------------------------------------- #


def _seed_route_days_2_and_4() -> WorkingRoute:
    """Build a route with stops at day-2 (FRA) and day-4 (LIS) via two tickets."""
    route = WorkingRoute()
    update_route(
        route,
        _ticket_one_leg(
            source_id="seed-01",
            from_city="JFK",
            to_city="FRA",
            departure="2027-03-02T00:00:00Z",
            arrival="2027-03-02T06:00:00Z",
        ),
    )
    update_route(
        route,
        _ticket_one_leg(
            source_id="seed-02",
            from_city="FRA",
            to_city="LIS",
            departure="2027-03-04T08:00:00Z",
            arrival="2027-03-04T11:00:00Z",
        ),
    )
    return route


def test_build_ops_inserts_at_front_when_earlier_than_all() -> None:
    """A new ticket on day 1 should pick ``after="start"`` semantics (None)."""
    route = _seed_route_days_2_and_4()
    # Earlier-than-everything single-leg ticket (LHR -> JFK) arriving day 1.
    fragment = _ticket_one_leg(
        source_id="doc-front-01",
        from_city="LHR",
        to_city="JFK",
        departure="2027-03-01T00:00:00Z",
        arrival="2027-03-01T06:00:00Z",
    )

    ops = build_ops(route, fragment)
    new_lhr = next(o for o in ops if isinstance(o, CreateStop) and o.city == "LHR")
    # JFK already exists in the route from the seed; only LHR is created. It
    # precedes everything, so it must prepend.
    assert new_lhr.after is None


def test_build_ops_inserts_in_middle_after_day_2_stop() -> None:
    """A day-3 ticket whose new from-city slots in after the day-2 stop.

    Uses a brand-new ``from`` city (BCN) so the classifier's condition (c)
    (per-traveler departure slot already filled on the existing FRA stop)
    doesn't trigger an unrelated CREATE on FRA. BCN — being entirely new —
    must be anchored chronologically against the existing day-2 FRA stop.
    """
    route = _seed_route_days_2_and_4()
    fra_id = next(s.id for s in route.stops if s.city == "FRA")

    # BCN -> MAD departing day 3 — both cities are new. BCN's departure
    # (day-3 08:00) and MAD's arrival (day-3 10:00) both fall between the
    # existing FRA (day 2) and LIS (day 4) stops.
    fragment = _ticket_one_leg(
        source_id="doc-middle-01",
        from_city="BCN",
        to_city="MAD",
        departure="2027-03-03T08:00:00Z",
        arrival="2027-03-03T10:00:00Z",
    )

    ops = build_ops(route, fragment)
    new_bcn = next(o for o in ops if isinstance(o, CreateStop) and o.city == "BCN")
    # BCN is the FIRST new stop in the batch, so it is anchored against the
    # existing route by `find_after_neighbor`, which picks day-2 FRA.
    assert new_bcn.after == fra_id


def test_build_ops_inserts_at_end_after_latest_stop() -> None:
    """A day-5 ticket appends after the latest existing stop (day-4 LIS)."""
    route = _seed_route_days_2_and_4()
    lis_id = next(s.id for s in route.stops if s.city == "LIS")

    fragment = _ticket_one_leg(
        source_id="doc-end-01",
        from_city="LIS",
        to_city="MAD",
        departure="2027-03-05T08:00:00Z",
        arrival="2027-03-05T11:00:00Z",
    )

    ops = build_ops(route, fragment)
    new_mad = next(o for o in ops if isinstance(o, CreateStop) and o.city == "MAD")
    assert new_mad.after == lis_id


# --------------------------------------------------------------------------- #
# find_after_neighbor — direct unit tests
# --------------------------------------------------------------------------- #


def test_find_after_neighbor_empty_route_returns_none() -> None:
    """An empty route has no anchor — prepend is the only option."""
    route = WorkingRoute()
    assert find_after_neighbor(route, _dt("2027-03-01T00:00:00Z")) is None


def test_find_after_neighbor_before_all_returns_none() -> None:
    """A time earlier than every existing stop returns None (prepend)."""
    route = _seed_route_days_2_and_4()
    assert find_after_neighbor(route, _dt("2027-03-01T00:00:00Z")) is None


def test_find_after_neighbor_in_middle_picks_latest_le() -> None:
    """A time mid-route picks the latest existing stop whose time is <= new."""
    route = _seed_route_days_2_and_4()
    fra_id = next(s.id for s in route.stops if s.city == "FRA")
    assert find_after_neighbor(route, _dt("2027-03-03T00:00:00Z")) == fra_id


def test_find_after_neighbor_after_all_picks_last() -> None:
    """A time later than every stop picks the latest stop in time."""
    route = _seed_route_days_2_and_4()
    lis_id = next(s.id for s in route.stops if s.city == "LIS")
    assert find_after_neighbor(route, _dt("2027-03-10T00:00:00Z")) == lis_id


def test_find_after_neighbor_falls_back_to_last_when_no_timed_stops() -> None:
    """A route with only untimed stops appends to the back as a safe fallback."""
    route = WorkingRoute()
    # Build a synthetic untimed stop without going through the applier.
    route.stops.append(RouteStop(id=route.mint_stop_id(), city="ROM"))
    assert find_after_neighbor(route, _dt("2027-03-01T00:00:00Z")) == "stop-1"


# --------------------------------------------------------------------------- #
# rules.build_ops — out-of-scope guards
# --------------------------------------------------------------------------- #


def test_build_ops_rejects_hotel_fragment() -> None:
    """Hotel bookings are still out of scope until Slice 4."""
    route = WorkingRoute()
    with pytest.raises(RuleNotImplementedError, match="hotel"):
        build_ops(route, _hotel_booking())


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
    """Out-of-scope shapes (hotel) surface as EngineError chained from the marker."""
    route = WorkingRoute()
    with pytest.raises(EngineError) as exc_info:
        update_route(route, _hotel_booking())

    assert isinstance(exc_info.value.__cause__, RuleNotImplementedError)
    # Route was not mutated — no ops applied.
    assert route.stops == []
    assert route.transits == []


# --------------------------------------------------------------------------- #
# Slice 3 — per-traveler-per-slot identity classifier
# --------------------------------------------------------------------------- #


def _multi_leg_ticket(
    *,
    source_id: str,
    legs: list[tuple[str, str, str, str]],
    travelers: list[str] | None = None,
) -> TransitTicketFragment:
    """Build a multi-leg ticket from ``(from, to, departure, arrival)`` tuples."""
    return TransitTicketFragment.model_validate(
        {
            "documentType": "air-ticket",
            "sourceDocumentId": source_id,
            "pnr": "PNR" + source_id[-3:],
            "travelers": travelers or ["traveler-1"],
            "legs": [
                {
                    "from": frm,
                    "to": to,
                    "departureAt": dep,
                    "arrivalAt": arr,
                }
                for (frm, to, dep, arr) in legs
            ],
        }
    )


def test_classifier_forward_circle_creates_second_same_city_stop() -> None:
    """LED -> MOW -> BEG -> MOW on an empty route yields TWO distinct MOW stops.

    Condition (b), forward direction: within ONE multi-leg ticket the city MOW
    appears twice with BEG sitting between the two MOW events in time, so the
    second MOW must be a NEW stop. The closing-leg transit BEG -> MOW must
    wire to the SECOND MOW, never collapse into the first.
    """
    route = WorkingRoute()
    fragment = _multi_leg_ticket(
        source_id="circle-01",
        legs=[
            ("LED", "MOW", "2027-04-01T08:00:00Z", "2027-04-01T10:00:00Z"),
            ("MOW", "BEG", "2027-04-02T08:00:00Z", "2027-04-02T11:00:00Z"),
            ("BEG", "MOW", "2027-04-03T08:00:00Z", "2027-04-03T11:00:00Z"),
        ],
    )

    update_route(route, fragment)

    cities = [s.city for s in route.stops]
    assert cities.count("MOW") == 2, f"expected two MOW stops, got {cities!r}"
    assert cities == ["LED", "MOW", "BEG", "MOW"]
    # The closing BEG -> MOW must wire to the SECOND MOW (the day-3 arrival).
    closing = next(
        t for t in route.transits if t.departure_at == _dt("2027-04-03T08:00:00Z")
    )
    # The second MOW is the last stop in the route.
    assert closing.to_stop_id == route.stops[-1].id
    assert route.stops[-1].arrival_at == _dt("2027-04-03T11:00:00Z")


def test_classifier_straight_return_creates_second_origin_stop() -> None:
    """Closing leg back to the start city becomes a SECOND origin stop.

    Slice-2's wedge: a straight ticket A->B->C followed by a separate ticket
    C->A. The closing arrival at A is chronologically AFTER an intervening
    different-city stop, so condition (b) triggers a CREATE — not a reuse of
    the original origin stop.
    """
    route = WorkingRoute()
    outbound = _multi_leg_ticket(
        source_id="straight-out-01",
        legs=[
            ("JFK", "ATH", "2027-03-01T00:00:00Z", "2027-03-01T08:00:00Z"),
            ("ATH", "ROM", "2027-03-02T00:00:00Z", "2027-03-02T03:00:00Z"),
        ],
    )
    closing = _multi_leg_ticket(
        source_id="straight-return-02",
        legs=[("ROM", "JFK", "2027-03-03T00:00:00Z", "2027-03-03T08:00:00Z")],
    )

    update_route(route, outbound)
    update_route(route, closing)

    cities = [s.city for s in route.stops]
    assert cities == ["JFK", "ATH", "ROM", "JFK"], (
        f"closing-leg JFK should be a DISTINCT second origin stop, got {cities!r}"
    )
    # And the new JFK carries the closing-arrival time, not the original.
    assert route.stops[-1].arrival_at == _dt("2027-03-03T08:00:00Z")
    assert route.stops[0].departure_at == _dt("2027-03-01T00:00:00Z")


def test_classifier_reverse_earlier_LHR_revisit_creates_new_lhr_at_front() -> None:
    """Pre-existing later LHR + new earlier outbound MXP->LHR->HEL->MAD.

    Condition (b), EARLIER direction (the case that defeated Sonnet at 79.2%).
    Route already holds stop-2 = LHR with arrival day 4 (learned from a
    closing leg JFK -> LHR), plus HEL / MAD / JFK on days 2-4. A new fragment
    is the original outbound MXP -> LHR -> HEL -> MAD whose LHR arrival is
    day 1. Because HEL / MAD / JFK sit between day 1 and day 4 in time, the
    new LHR is a DIFFERENT, EARLIER visit — create a second LHR at the front
    of the route, never merge into stop-2.
    """
    route = WorkingRoute()
    # Seed: HEL -> MAD -> JFK -> LHR (days 2..4) — closing leg learned first.
    seed = _multi_leg_ticket(
        source_id="seed-rev-01",
        legs=[
            ("HEL", "MAD", "2027-05-02T08:00:00Z", "2027-05-02T11:00:00Z"),
            ("MAD", "JFK", "2027-05-03T08:00:00Z", "2027-05-03T16:00:00Z"),
            ("JFK", "LHR", "2027-05-04T00:00:00Z", "2027-05-04T08:00:00Z"),
        ],
    )
    update_route(route, seed)

    # Sanity on the seed.
    assert [s.city for s in route.stops] == ["HEL", "MAD", "JFK", "LHR"]
    seed_lhr_id = route.stops[-1].id

    # Outbound: MXP -> LHR (day 1) -> HEL (day 2) -> MAD (day 2)
    outbound = _multi_leg_ticket(
        source_id="rev-out-02",
        legs=[
            ("MXP", "LHR", "2027-05-01T08:00:00Z", "2027-05-01T10:00:00Z"),
            ("LHR", "HEL", "2027-05-02T00:00:00Z", "2027-05-02T03:00:00Z"),
            ("HEL", "MAD", "2027-05-02T08:00:00Z", "2027-05-02T11:00:00Z"),
        ],
    )

    update_route(route, outbound)

    cities = [s.city for s in route.stops]
    # Two LHR stops — the new EARLIER one must NOT have merged into seed_lhr.
    assert cities.count("LHR") == 2, f"expected two LHR stops, got {cities!r}"
    # The seed LHR keeps its day-4 arrival and identity.
    seed_lhr = route.stop_by_id(seed_lhr_id)
    assert seed_lhr is not None
    assert seed_lhr.arrival_at == _dt("2027-05-04T08:00:00Z")
    # The new LHR sits at the FRONT (before HEL/MAD/JFK).
    front_lhr_idx = cities.index("LHR")
    assert front_lhr_idx == 1, (
        f"new LHR should be at index 1 (after the newly-front MXP), got {cities!r}"
    )
    # The new LHR carries the day-1 arrival.
    front_lhr = route.stops[front_lhr_idx]
    assert front_lhr.arrival_at == _dt("2027-05-01T10:00:00Z")
    # MXP is the new origin.
    assert route.stops[0].city == "MXP"


def test_classifier_per_traveler_slot_conflict_triggers_create() -> None:
    """Same city, same traveler, same role, DIFFERENT time → CREATE (condition c).

    With no intervening different-city stop to invoke (b), the per-traveler
    slot conflict (an existing arrival for this traveler at a different time)
    is what proves the new event is a separate visit.
    """
    route = WorkingRoute()
    # Seed: a single transit X -> Y arriving day 2 (one traveler).
    first = _ticket_one_leg(
        source_id="seed-slot-01",
        from_city="JFK",
        to_city="ATH",
        departure="2027-06-01T00:00:00Z",
        arrival="2027-06-01T08:00:00Z",
    )
    update_route(route, first)
    ath_id = next(s.id for s in route.stops if s.city == "ATH")

    # Second ticket: another arrival into ATH for the SAME traveler at a
    # different time (no city between to trigger (b)).
    second = _ticket_one_leg(
        source_id="slot-02",
        from_city="JFK",
        to_city="ATH",
        departure="2027-06-01T09:00:00Z",
        arrival="2027-06-01T12:00:00Z",
    )
    update_route(route, second)

    # Two ATH stops — slot conflict forces a CREATE.
    ath_stops = [s for s in route.stops if s.city == "ATH"]
    assert len(ath_stops) == 2, (
        f"expected two ATH stops, got {[s.city for s in route.stops]!r}"
    )
    # The original ATH keeps its identity + day-1 08:00 arrival.
    original = route.stop_by_id(ath_id)
    assert original is not None
    assert original.arrival_at == _dt("2027-06-01T08:00:00Z")


def test_classifier_sanity_check_flips_enrich_to_create() -> None:
    """Would-make ``arrival > departure`` on the target → CREATE.

    Seed JFK -> FRA arrives FRA day 5. New fragment is a fresh arrival into
    FRA at day 8 — there are no transits departing FRA yet AND no
    different-city stop sits between day 5 and day 8, so condition (b) and
    (c) both pass. But the seeded FRA already has departure_at unset, so the
    sanity check considers only arrival inversion. Use an enriched-departure
    seed to actually exercise the inversion: pre-set FRA's departure to day 6
    via an outbound transit; a later arrival at day 8 would put arrival
    after departure → CREATE.
    """
    route = WorkingRoute()
    # JFK -> FRA arriving day 5.
    update_route(
        route,
        _ticket_one_leg(
            source_id="sanity-seed-01",
            from_city="JFK",
            to_city="FRA",
            departure="2027-07-05T00:00:00Z",
            arrival="2027-07-05T06:00:00Z",
        ),
    )
    # FRA -> LIS departing day 6 → FRA gets departure_at=day 6.
    update_route(
        route,
        _ticket_one_leg(
            source_id="sanity-seed-02",
            from_city="FRA",
            to_city="LIS",
            departure="2027-07-06T08:00:00Z",
            arrival="2027-07-06T11:00:00Z",
        ),
    )
    fra_id_before = next(s.id for s in route.stops if s.city == "FRA")

    # New arrival into FRA at day 8 — LATER than departure (day 6). Sanity
    # check must flip ENRICH→CREATE. Use a from-city that's brand new (BCN)
    # so condition (b) doesn't trigger via an intervening LIS stop; here LIS
    # at day 6 sits between FRA day 5 and the new day 8 arrival, so (b)
    # ALSO triggers — but the sanity check would catch it even if (b) didn't.
    update_route(
        route,
        _ticket_one_leg(
            source_id="sanity-late-03",
            from_city="BCN",
            to_city="FRA",
            departure="2027-07-08T06:00:00Z",
            arrival="2027-07-08T10:00:00Z",
        ),
    )

    fra_stops = [s for s in route.stops if s.city == "FRA"]
    assert len(fra_stops) == 2, (
        f"expected two FRA stops, got {[s.city for s in route.stops]!r}"
    )
    # Original FRA still has its day-5 arrival + day-6 departure.
    original = route.stop_by_id(fra_id_before)
    assert original is not None
    assert original.arrival_at == _dt("2027-07-05T06:00:00Z")
    assert original.departure_at == _dt("2027-07-06T08:00:00Z")


def test_classifier_pure_function_call_returns_create_for_unseen_city() -> None:
    """Direct call to :func:`classify_event` — condition (a)."""
    from spikes.route_engine_algorithmic.rules import (
        Decision,
        DecisionKind,
        Event,
        EventRole,
        classify_event,
    )

    route = WorkingRoute()
    event = Event(
        city="ABC",
        time=_dt("2027-08-01T00:00:00Z"),
        role=EventRole.ARRIVAL,
        travelers=("traveler-1",),
    )
    decision = classify_event(route, event)
    assert decision == Decision.create()
    assert decision.kind is DecisionKind.CREATE
