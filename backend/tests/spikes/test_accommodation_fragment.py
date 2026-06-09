"""DUS-31 Slice 4 — multi-accommodation fragment behaviour.

Covers shape and rules-engine paths that the regenerated 192-scenario corpus
does NOT exercise today (the generator emits exactly one entry per fragment).
Slice 6's adapter and Slice 7+ scenarios may emit multi-entry fragments, so
the engine has to handle them correctly the moment they appear.

Tests:

- Round-trip parsing of a 2-accommodation ``AccommodationFragment`` through
  the discriminated ``Fragment`` union — confirms the JSON shape mirrors the
  schema.
- Multi-accommodation rules path (two different cities) producing two stops
  with the right ``Accommodation`` (kind + identifier + dates) on each.
- CREATE-ENRICH ambiguity guard: two non-overlapping bookings in the SAME
  city → two stops, locking in the per-batch ledger's awareness of an
  earlier in-fragment same-city addition.
- Pydantic ``Accommodation`` rejects missing ``kind`` / ``identifier``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from spikes.route_engine_algorithmic.engine import update_route
from spikes.route_engine_llm.models import (
    Accommodation,
    AccommodationFragment,
    Fragment,
    WorkingRoute,
)


_FRAGMENT_ADAPTER: TypeAdapter[Fragment] = TypeAdapter(Fragment)


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Round-trip parsing
# --------------------------------------------------------------------------- #


def test_accommodation_fragment_two_entries_two_cities_roundtrip() -> None:
    """A fragment with two accommodations in two cities loads via the union."""
    payload = {
        "documentType": "hotel-booking",
        "sourceDocumentId": "doc-multi-01",
        "confirmationCode": "MULTI-001",
        "travelers": ["traveler-1"],
        "cities": ["Paris", "Lisbon"],
        "accommodations": [
            {
                "city": "Paris",
                "kind": "hotel",
                "identifier": "Hotel Lutetia",
                "checkInAt": "2027-04-01T15:00:00Z",
                "checkOutAt": "2027-04-03T11:00:00Z",
            },
            {
                "city": "Lisbon",
                "kind": "hotel",
                "identifier": "Hotel Lisboa",
                "checkInAt": "2027-04-05T15:00:00Z",
                "checkOutAt": "2027-04-07T11:00:00Z",
            },
        ],
    }

    # Round-trip via JSON to exercise the deserializer the corpus loader uses.
    fragment = _FRAGMENT_ADAPTER.validate_python(json.loads(json.dumps(payload)))

    assert isinstance(fragment, AccommodationFragment)
    assert fragment.document_type == "hotel-booking"
    assert fragment.cities == ["Paris", "Lisbon"]
    assert len(fragment.accommodations) == 2
    paris, lisbon = fragment.accommodations
    assert paris.city == "Paris"
    assert paris.kind == "hotel"
    assert paris.identifier == "Hotel Lutetia"
    assert paris.check_in_at == _dt("2027-04-01T15:00:00Z")
    assert paris.check_out_at == _dt("2027-04-03T11:00:00Z")
    assert lisbon.city == "Lisbon"
    assert lisbon.identifier == "Hotel Lisboa"


# --------------------------------------------------------------------------- #
# Multi-accommodation rules path
# --------------------------------------------------------------------------- #


def _multi_accom_fragment(
    *,
    source_id: str,
    entries: list[tuple[str, str, str, str]],
    travelers: list[str] | None = None,
) -> AccommodationFragment:
    """Build a fragment from ``(city, identifier, check_in, check_out)`` tuples."""
    cities: list[str] = []
    for city, *_ in entries:
        if city not in cities:
            cities.append(city)
    return AccommodationFragment.model_validate(
        {
            "documentType": "hotel-booking",
            "sourceDocumentId": source_id,
            "confirmationCode": "C" + source_id[-3:],
            "travelers": travelers or ["traveler-1"],
            "cities": cities,
            "accommodations": [
                {
                    "city": city,
                    "kind": "hotel",
                    "identifier": identifier,
                    "checkInAt": check_in,
                    "checkOutAt": check_out,
                }
                for city, identifier, check_in, check_out in entries
            ],
        }
    )


def test_multi_accommodation_two_cities_create_create_on_empty_route() -> None:
    """Two accommodations in two different cities on an empty route → two stops.

    Drives the engine via :func:`update_route` (not the rules helper) so the
    test mirrors the production replay path. Each accommodation must mint a
    distinct stop and attach its own ``Accommodation`` carrying the right
    kind + identifier + dates.
    """
    route = WorkingRoute()
    fragment = _multi_accom_fragment(
        source_id="multi-create-01",
        entries=[
            ("Paris", "Hotel Lutetia", "2027-04-01T15:00:00Z", "2027-04-03T11:00:00Z"),
            ("Lisbon", "Hotel Lisboa", "2027-04-05T15:00:00Z", "2027-04-07T11:00:00Z"),
        ],
    )
    update_route(route, fragment)

    assert len(route.stops) == 2
    paris_stop, lisbon_stop = route.stops
    assert paris_stop.city == "Paris"
    assert lisbon_stop.city == "Lisbon"

    assert len(paris_stop.accommodations) == 1
    paris_accom = paris_stop.accommodations[0]
    assert paris_accom.kind == "hotel"
    assert paris_accom.identifier == "Hotel Lutetia"
    assert paris_accom.check_in_at == _dt("2027-04-01T15:00:00Z")
    assert paris_accom.check_out_at == _dt("2027-04-03T11:00:00Z")

    assert len(lisbon_stop.accommodations) == 1
    lisbon_accom = lisbon_stop.accommodations[0]
    assert lisbon_accom.identifier == "Hotel Lisboa"
    assert lisbon_accom.check_in_at == _dt("2027-04-05T15:00:00Z")

    # Both stops should carry the fragment's traveler.
    assert paris_stop.travelers == ["traveler-1"]
    assert lisbon_stop.travelers == ["traveler-1"]


def test_multi_accommodation_same_city_nonoverlapping_dates_create_create() -> None:
    """Two same-city accommodations with non-overlapping dates → two stops.

    The pending-projection ledger must see the in-fragment first booking when
    classifying the second one. With both bookings naming the same city but
    distinct check-in times, condition (c) treats the slot as filled and
    forces CREATE for the second entry — matching the
    ``test_hotel_two_non_overlapping_same_city_bookings_split_into_two_stops``
    cross-fragment behaviour, but proving it works WITHIN a single fragment
    too.
    """
    route = WorkingRoute()
    fragment = _multi_accom_fragment(
        source_id="multi-split-01",
        entries=[
            (
                "Barcelona",
                "Riverside Inn",
                "2027-07-01T03:00:00Z",
                "2027-07-01T21:00:00Z",
            ),
            (
                "Barcelona",
                "Plaza Suites",
                "2027-07-03T15:00:00Z",
                "2027-07-04T09:00:00Z",
            ),
        ],
    )
    update_route(route, fragment)

    bcn_stops = [s for s in route.stops if s.city == "Barcelona"]
    assert len(bcn_stops) == 2, (
        "in-fragment second booking must CREATE, not collapse onto the first; "
        f"got {[s.id for s in bcn_stops]!r}"
    )

    first, second = sorted(
        bcn_stops, key=lambda s: s.accommodations[0].check_in_at
    )
    assert len(first.accommodations) == 1
    assert first.accommodations[0].identifier == "Riverside Inn"
    assert len(second.accommodations) == 1
    assert second.accommodations[0].identifier == "Plaza Suites"


# --------------------------------------------------------------------------- #
# Working-route Accommodation requires kind + identifier
# --------------------------------------------------------------------------- #


def test_accommodation_requires_kind() -> None:
    """Pydantic rejects an ``Accommodation`` missing ``kind``."""
    with pytest.raises(ValidationError):
        Accommodation.model_validate(
            {
                "checkInAt": "2027-04-01T15:00:00Z",
                "checkOutAt": "2027-04-03T11:00:00Z",
                "identifier": "Hotel Lutetia",
            }
        )


def test_accommodation_requires_identifier() -> None:
    """Pydantic rejects an ``Accommodation`` missing ``identifier``."""
    with pytest.raises(ValidationError):
        Accommodation.model_validate(
            {
                "checkInAt": "2027-04-01T15:00:00Z",
                "checkOutAt": "2027-04-03T11:00:00Z",
                "kind": "hotel",
            }
        )
