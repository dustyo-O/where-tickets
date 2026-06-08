"""Tests for the printed-city-name identity normalizer (Slice 2 of DUS-31).

The engine identifies cities by their printed name with a simple normalizer
(:func:`spikes.route_engine_llm.models.city_identity`): strings that differ
only by case or surrounding whitespace refer to the same city. The original
casing is preserved on :class:`RouteStop.city` for display.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from spikes.route_engine_algorithmic.engine import update_route
from spikes.route_engine_llm.models import (
    TransitTicketFragment,
    WorkingRoute,
    city_identity,
)


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=UTC)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Paris", "PARIS"),
        ("Paris", "paris"),
        ("Paris", "  Paris  "),
        ("  paris  ", "PARIS"),
        ("New York", "NEW YORK"),
    ],
)
def test_city_identity_collapses_case_and_whitespace_variants(a: str, b: str) -> None:
    assert city_identity(a) == city_identity(b)


def test_city_identity_keeps_distinct_cities_distinct() -> None:
    assert city_identity("Paris") != city_identity("Berlin")


def test_paris_and_PARIS_collapse_to_one_stop() -> None:
    """Two fragments naming the same city with different casing must share a stop.

    Fragment A flies Warsaw -> Paris. Fragment B flies Paris (printed
    ``"PARIS"``) -> Berlin. The engine must collapse both Paris mentions onto
    a single stop and keep whichever casing it saw first as the printed city
    on the stop.
    """
    fragment_a = TransitTicketFragment.model_validate(
        {
            "documentType": "air-ticket",
            "sourceDocumentId": "doc-a",
            "pnr": "AAA111",
            "travelers": ["traveler-1"],
            "legs": [
                {
                    "from": "Warsaw",
                    "to": "Paris",
                    "departureAt": "2027-03-01T08:00:00Z",
                    "arrivalAt": "2027-03-01T11:00:00Z",
                }
            ],
        }
    )
    fragment_b = TransitTicketFragment.model_validate(
        {
            "documentType": "air-ticket",
            "sourceDocumentId": "doc-b",
            "pnr": "BBB222",
            "travelers": ["traveler-1"],
            "legs": [
                {
                    "from": "PARIS",
                    "to": "Berlin",
                    "departureAt": "2027-03-02T08:00:00Z",
                    "arrivalAt": "2027-03-02T10:00:00Z",
                }
            ],
        }
    )

    route = WorkingRoute()
    update_route(route, fragment_a)
    update_route(route, fragment_b)

    cities_normalized = [city_identity(s.city) for s in route.stops]
    assert cities_normalized == [
        city_identity("Warsaw"),
        city_identity("Paris"),
        city_identity("Berlin"),
    ], f"expected a single Paris stop, got: {[s.city for s in route.stops]!r}"

    # Identity normalization preserves the printed name on the stored stop —
    # the first fragment named Paris with title case so that's what survives.
    paris_stop = next(s for s in route.stops if city_identity(s.city) == "paris")
    assert paris_stop.city == "Paris"


def test_paris_whitespace_variant_collapses_to_one_stop() -> None:
    """``"  paris  "`` (whitespace + lowercase) must collapse with ``"Paris"``."""
    fragment_a = TransitTicketFragment.model_validate(
        {
            "documentType": "air-ticket",
            "sourceDocumentId": "doc-a",
            "pnr": "AAA111",
            "travelers": ["traveler-1"],
            "legs": [
                {
                    "from": "Warsaw",
                    "to": "Paris",
                    "departureAt": "2027-03-01T08:00:00Z",
                    "arrivalAt": "2027-03-01T11:00:00Z",
                }
            ],
        }
    )
    fragment_b = TransitTicketFragment.model_validate(
        {
            "documentType": "air-ticket",
            "sourceDocumentId": "doc-b",
            "pnr": "BBB222",
            "travelers": ["traveler-1"],
            "legs": [
                {
                    "from": "  paris  ",
                    "to": "Berlin",
                    "departureAt": "2027-03-02T08:00:00Z",
                    "arrivalAt": "2027-03-02T10:00:00Z",
                }
            ],
        }
    )

    route = WorkingRoute()
    update_route(route, fragment_a)
    update_route(route, fragment_b)

    assert [city_identity(s.city) for s in route.stops] == [
        city_identity("Warsaw"),
        city_identity("Paris"),
        city_identity("Berlin"),
    ]
