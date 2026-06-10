"""Offline unit tests for the corpus loader and the three scoring checks.

No network, no DB, no AWS — pure in-memory pydantic + scoring functions over
the committed corpus.
"""

from __future__ import annotations

from datetime import UTC, datetime

from spikes.route_engine_llm.corpus import (
    ExpectedRoute,
    Scenario,
    load_scenario,
)
from spikes.route_engine_llm.models import (
    Accommodation,
    RouteStop,
    Transit,
    WorkingRoute,
)
from spikes.route_engine_llm.operations import apply
from spikes.route_engine_llm.scoring import (
    FailureCategory,
    final_route_match,
    identity_preserved,
    ordering_consistent,
    score_scenario,
)


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Builders: turn an ExpectedRoute into an equivalent WorkingRoute
# --------------------------------------------------------------------------- #


def _working_from_expected(expected: ExpectedRoute) -> WorkingRoute:
    """Build a WorkingRoute equivalent to ``expected`` and rely on projection.

    Stops get sequential ``stop-N`` IDs in expected order and carry only their
    accommodations (not derivable from transits). Transits resolve ``from``/``to``
    cities to those IDs; when a city appears more than once (a circle), transits
    are wired to stops in route order so the resolved city sequence matches.

    Stop timing/travelers are NOT hand-authored: after wiring stops + transits we
    let the applier's projection derive each stop's ``arrivalAt``/``departureAt``/
    ``travelers`` from the incident transits — exactly as the live engine does. A
    stop with NO incident transit (e.g. accommodation-only) cannot be derived, so
    its expected timing/travelers are set explicitly first (the override path).
    """
    route = WorkingRoute()
    for stop in expected.stops:
        route.stops.append(
            RouteStop(
                id=route.mint_stop_id(),
                city=stop.city,
                accommodations=[
                    Accommodation(
                        checkInAt=a.check_in_at,
                        checkOutAt=a.check_out_at,
                        kind=a.kind,
                        identifier=a.identifier,
                    )
                    for a in stop.accommodations
                ],
            )
        )

    # Disambiguate repeated cities (circles) by walking the stop sequence in
    # order: each transit's `from` is the first matching stop at/after a running
    # cursor, and its `to` is the first match strictly after that `from`.
    stops_in_order = route.stops

    def _find(city: str, start: int) -> int:
        index = next(
            (
                i
                for i in range(start, len(stops_in_order))
                if stops_in_order[i].city == city
            ),
            None,
        )
        if index is None:
            msg = f"no stop {city!r} at/after index {start} for transit wiring"
            raise AssertionError(msg)
        return index

    cursor = 0
    transit_stop_ids: set[str] = set()
    for transit in expected.transits:
        from_index = _find(transit.from_, cursor)
        to_index = _find(transit.to, from_index + 1)
        from_id = stops_in_order[from_index].id
        to_id = stops_in_order[to_index].id
        cursor = from_index
        transit_stop_ids.add(from_id)
        transit_stop_ids.add(to_id)
        route.transits.append(
            Transit(
                id=route.mint_transit_id(),
                fromStopId=from_id,
                toStopId=to_id,
                mode=transit.mode,
                departureAt=transit.departure_at,
                arrivalAt=transit.arrival_at,
                travelers=list(transit.travelers),
                sourceFragmentId=transit.source_fragment_id,
            )
        )

    # No-transit stops can't be derived; set their expected fields explicitly
    # (override/fallback path) so projection only fills the ticketed stops.
    for working_stop, expected_stop in zip(route.stops, expected.stops, strict=True):
        if working_stop.id not in transit_stop_ids:
            working_stop.arrival_at = expected_stop.arrival_at
            working_stop.departure_at = expected_stop.departure_at
            working_stop.travelers = list(expected_stop.travelers)

    # Empty op batch triggers the applier's end-of-apply stop projection,
    # deriving arrivalAt/departureAt/travelers on the ticketed stops.
    apply(route, [])
    return route


# --------------------------------------------------------------------------- #
# Corpus loader
# --------------------------------------------------------------------------- #


def test_load_scenario_orders_fragments_and_parses_expected() -> None:
    scenario = load_scenario("064-circle-1p-forward")

    assert isinstance(scenario, Scenario)
    assert scenario.name == "064-circle-1p-forward"
    # 01-bus-ticket.json then 02-air-ticket.json, in filename order.
    assert [f.document_type for f in scenario.fragments] == [
        "bus-ticket",
        "air-ticket",
    ]
    # Helsinki appears as two distinct stops in this circle scenario
    # (the circle's revisit city; post-DUS-31-Slice-11 city pool reshuffle
    # the doubled city is Helsinki, was Rome).
    cities = [s.city for s in scenario.expected.stops]
    assert cities == ["Stockholm", "Helsinki", "Paris", "Barcelona", "Helsinki"]
    assert cities.count("Helsinki") == 2


# --------------------------------------------------------------------------- #
# Check 1: final_route_match self-match (accepts a correct route)
# --------------------------------------------------------------------------- #


def test_final_route_match_self_match_straight() -> None:
    expected = load_scenario("000-straight-1p-forward").expected
    working = _working_from_expected(expected)

    result = final_route_match(working, expected)

    assert result.passed
    assert result.category is None


def test_final_route_match_self_match_circle_double_helsinki() -> None:
    expected = load_scenario("064-circle-1p-forward").expected
    working = _working_from_expected(expected)

    # Sanity: two distinct Helsinki stop IDs survive into the working route
    # (the doubled city in this circle scenario after the DUS-31 Slice-11
    # seed reshuffle — was Rome pre-Slice-11).
    helsinki_ids = [s.id for s in working.stops if s.city == "Helsinki"]
    assert len(helsinki_ids) == 2

    result = final_route_match(working, expected)

    assert result.passed


def test_final_route_match_detects_field_mismatch() -> None:
    expected = load_scenario("000-straight-1p-forward").expected
    working = _working_from_expected(expected)
    # Perturb a single field: change one stop's arrival time.
    working.stops[1].arrival_at = _dt("2099-01-01T00:00:00Z")

    result = final_route_match(working, expected)

    assert not result.passed
    assert result.category is FailureCategory.FINAL_MISMATCH


def test_final_route_match_transits_compared_as_set() -> None:
    expected = load_scenario("000-straight-1p-forward").expected
    working = _working_from_expected(expected)
    # Reversing transit order must not matter — they compare as a set.
    working.transits.reverse()

    assert final_route_match(working, expected).passed


# --------------------------------------------------------------------------- #
# Check 2: identity_preserved
# --------------------------------------------------------------------------- #


def _snapshot(stops: list[tuple[str, str]]) -> WorkingRoute:
    """Build a snapshot from (id, city) pairs in order."""
    route = WorkingRoute()
    route.stops = [RouteStop(id=sid, city=city) for sid, city in stops]
    return route


def test_identity_preserved_passes_on_append_only() -> None:
    snapshots = [
        _snapshot([("stop-1", "HEL")]),
        _snapshot([("stop-1", "HEL"), ("stop-2", "ROM")]),
        _snapshot([("stop-1", "HEL"), ("stop-2", "ROM"), ("stop-3", "LIS")]),
    ]

    assert identity_preserved(snapshots).passed


def test_identity_preserved_fails_when_id_disappears() -> None:
    snapshots = [
        _snapshot([("stop-1", "HEL"), ("stop-2", "ROM")]),
        # stop-2 vanished — destroy-and-rebuild signature.
        _snapshot([("stop-1", "HEL")]),
    ]

    result = identity_preserved(snapshots)

    assert not result.passed
    assert result.category is FailureCategory.IDENTITY_VIOLATION


def test_identity_preserved_fails_when_city_changes_under_reused_id() -> None:
    snapshots = [
        _snapshot([("stop-1", "HEL")]),
        # stop-1 silently re-pointed at a different city.
        _snapshot([("stop-1", "ROM")]),
    ]

    result = identity_preserved(snapshots)

    assert not result.passed
    assert result.category is FailureCategory.IDENTITY_VIOLATION


# --------------------------------------------------------------------------- #
# Check 3: ordering_consistent (gap tolerance)
# --------------------------------------------------------------------------- #


def test_ordering_consistent_passes_with_gaps() -> None:
    # Final order: HEL, ROM, LIS, CDG. Earlier steps show subsets in order,
    # including a gapped subset (HEL then CDG, skipping ROM/LIS).
    final = _snapshot(
        [("stop-1", "HEL"), ("stop-2", "ROM"), ("stop-3", "LIS"), ("stop-4", "CDG")]
    )
    snapshots = [
        _snapshot([("stop-1", "HEL"), ("stop-4", "CDG")]),
        _snapshot([("stop-1", "HEL"), ("stop-3", "LIS"), ("stop-4", "CDG")]),
        final,
    ]

    assert ordering_consistent(snapshots).passed


def test_ordering_consistent_fails_on_reorder() -> None:
    final = _snapshot([("stop-1", "HEL"), ("stop-2", "ROM"), ("stop-3", "LIS")])
    snapshots = [
        # Known stops appear in the wrong order relative to final.
        _snapshot([("stop-3", "LIS"), ("stop-1", "HEL")]),
        final,
    ]

    result = ordering_consistent(snapshots)

    assert not result.passed
    assert result.category is FailureCategory.ORDERING_VIOLATION


# --------------------------------------------------------------------------- #
# Aggregate: score_scenario
# --------------------------------------------------------------------------- #


def test_score_scenario_all_pass() -> None:
    expected = load_scenario("064-circle-1p-forward").expected
    final = _working_from_expected(expected)
    # A plausible append-only, order-consistent replay ending at `final`.
    snapshots = [
        _snapshot([(s.id, s.city) for s in final.stops[:2]]),
        final,
    ]

    result = score_scenario(snapshots, expected)

    assert result.passed
    assert result.category is None


def test_score_scenario_reports_final_mismatch() -> None:
    expected = load_scenario("000-straight-1p-forward").expected
    final = _working_from_expected(expected)
    final.stops[0].city = "XXX"  # diverge from expected, identity still intact
    snapshots = [final]

    result = score_scenario(snapshots, expected)

    assert not result.passed
    assert result.category is FailureCategory.FINAL_MISMATCH


def test_score_scenario_reports_identity_violation() -> None:
    expected = load_scenario("000-straight-1p-forward").expected
    final = _working_from_expected(expected)
    snapshots = [
        _snapshot([(s.id, s.city) for s in final.stops]),
        # A stop disappears mid-replay before the (correct) final snapshot.
        _snapshot([(final.stops[0].id, final.stops[0].city)]),
        final,
    ]

    result = score_scenario(snapshots, expected)

    assert not result.passed
    assert result.category is FailureCategory.IDENTITY_VIOLATION


def test_score_scenario_reports_ordering_violation() -> None:
    expected = load_scenario("000-straight-1p-forward").expected
    final = _working_from_expected(expected)
    reordered = _snapshot(
        [
            (final.stops[2].id, final.stops[2].city),
            (final.stops[0].id, final.stops[0].city),
        ]
    )
    snapshots = [reordered, final]

    result = score_scenario(snapshots, expected)

    assert not result.passed
    assert result.category is FailureCategory.ORDERING_VIOLATION
