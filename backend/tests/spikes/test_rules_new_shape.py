"""DUS-31 Slice 5 — airbnb, supplementary, venues, unattached docs.

Covers the new fragment shapes Slice 5 introduces:

- Airbnb fragments route identically to hotel fragments (same accommodation
  pipeline, only ``kind`` differs).
- Venues with a city land on the right stop (existing same-city stop wins;
  otherwise CREATE a new stop).
- Supplementary fragments with no routable place land as one
  :class:`UnattachedDocument` on the working route — without touching stops,
  transits, or the id counters.
- Supplementary fragments with at least one routable place are routed and
  NOT also unattached.
- Multi-venue same-city fragments attach both venues to the same stop and
  the in-batch ledger dedupes a malformed repeat of the same
  ``(kind, identifier)``.
- ``scoring.final_route_match`` ignores ``RouteStop.venues`` and
  ``WorkingRoute.unattached_documents`` entirely — the new working-route
  surface area can grow without breaking comparison against an
  expected-route that lacks those fields.
"""

from __future__ import annotations

from datetime import UTC, datetime

from spikes.route_engine_algorithmic.engine import update_route
from spikes.route_engine_llm.corpus import ExpectedRoute
from spikes.route_engine_llm.models import (
    AccommodationFragment,
    Price,
    RouteStop,
    SupplementaryFragment,
    Transit,
    TransitMode,
    UnattachedDocument,
    WorkingRoute,
)
from spikes.route_engine_llm.operations import AddUnattachedDocument, apply
from spikes.route_engine_llm.scoring import final_route_match


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _hotel_fragment(
    *,
    source_id: str,
    kind: str,
    city: str,
    identifier: str,
    check_in: str,
    check_out: str,
    travelers: list[str] | None = None,
) -> AccommodationFragment:
    document_type = "hotel-booking" if kind == "hotel" else "airbnb-booking"
    return AccommodationFragment.model_validate(
        {
            "documentType": document_type,
            "sourceDocumentId": source_id,
            "confirmationCode": "C" + source_id[-3:],
            "travelers": travelers or ["traveler-1"],
            "cities": [city],
            "accommodations": [
                {
                    "city": city,
                    "kind": kind,
                    "identifier": identifier,
                    "checkInAt": check_in,
                    "checkOutAt": check_out,
                }
            ],
        }
    )


def _supplementary_fragment(
    *,
    source_id: str,
    travelers: list[str] | None = None,
    cities: list[str] | None = None,
    venues: list[dict] | None = None,
    accommodations: list[dict] | None = None,
    stations: list[dict] | None = None,
    prices: list[dict] | None = None,
    qr_codes: list[str] | None = None,
) -> SupplementaryFragment:
    return SupplementaryFragment.model_validate(
        {
            "documentType": "supplementary",
            "sourceDocumentId": source_id,
            "travelers": travelers or ["traveler-1"],
            "cities": cities or [],
            "venues": venues or [],
            "accommodations": accommodations or [],
            "stations": stations or [],
            "prices": prices or [],
            "qrCodes": qr_codes or [],
        }
    )


# --------------------------------------------------------------------------- #
# Airbnb rides the accommodation path
# --------------------------------------------------------------------------- #


def test_airbnb_routed_identically_to_hotel() -> None:
    """Two parallel fragments — one hotel, one airbnb — produce structurally
    identical routes (modulo the ``kind`` / ``identifier`` on the attached
    accommodation)."""
    hotel = _hotel_fragment(
        source_id="hot-001",
        kind="hotel",
        city="Paris",
        identifier="Hotel Lutetia",
        check_in="2027-04-01T15:00:00Z",
        check_out="2027-04-03T11:00:00Z",
    )
    airbnb = _hotel_fragment(
        source_id="bnb-001",
        kind="airbnb",
        city="Paris",
        identifier="Charming Marais Loft",
        check_in="2027-04-01T15:00:00Z",
        check_out="2027-04-03T11:00:00Z",
    )

    hotel_route = WorkingRoute()
    update_route(hotel_route, hotel)
    airbnb_route = WorkingRoute()
    update_route(airbnb_route, airbnb)

    # Same number / shape of stops and transits.
    assert len(hotel_route.stops) == 1
    assert len(airbnb_route.stops) == 1
    assert hotel_route.transits == airbnb_route.transits  # both empty
    assert hotel_route.unattached_documents == airbnb_route.unattached_documents

    h_stop = hotel_route.stops[0]
    b_stop = airbnb_route.stops[0]
    assert h_stop.city == b_stop.city == "Paris"
    assert h_stop.travelers == b_stop.travelers == ["traveler-1"]
    assert len(h_stop.accommodations) == len(b_stop.accommodations) == 1

    # The only meaningful difference is the kind / identifier.
    h_accom = h_stop.accommodations[0]
    b_accom = b_stop.accommodations[0]
    assert h_accom.kind == "hotel"
    assert h_accom.identifier == "Hotel Lutetia"
    assert b_accom.kind == "airbnb"
    assert b_accom.identifier == "Charming Marais Loft"
    assert h_accom.check_in_at == b_accom.check_in_at
    assert h_accom.check_out_at == b_accom.check_out_at


# --------------------------------------------------------------------------- #
# Venue with city → attached to right stop
# --------------------------------------------------------------------------- #


def test_venue_with_city_attaches_to_existing_same_city_stop() -> None:
    """A supplementary venue in a city the route already has must ENRICH
    that stop (no new stop created)."""
    route = WorkingRoute()
    # Seed via a hotel booking so there's a Lisbon stop in the route.
    update_route(
        route,
        _hotel_fragment(
            source_id="hot-lis-01",
            kind="hotel",
            city="Lisbon",
            identifier="Hotel Lisboa",
            check_in="2027-05-01T15:00:00Z",
            check_out="2027-05-03T11:00:00Z",
        ),
    )
    assert len(route.stops) == 1
    initial_stop_id = route.stops[0].id

    update_route(
        route,
        _supplementary_fragment(
            source_id="sup-lis-01",
            cities=["Lisbon"],
            venues=[
                {
                    "city": "Lisbon",
                    "kind": "sightseeing",
                    "identifier": "Jeronimos Monastery",
                    "validFromAt": "2027-05-02T10:00:00Z",
                    "validToAt": "2027-05-02T12:00:00Z",
                }
            ],
        ),
    )

    assert len(route.stops) == 1, "venue must ENRICH the existing Lisbon stop"
    assert route.stops[0].id == initial_stop_id
    assert len(route.stops[0].venues) == 1
    venue = route.stops[0].venues[0]
    assert venue.kind == "sightseeing"
    assert venue.identifier == "Jeronimos Monastery"
    assert venue.valid_from_at == _dt("2027-05-02T10:00:00Z")
    assert venue.valid_to_at == _dt("2027-05-02T12:00:00Z")
    # Supplementary fragment carried at least one routable place — NOT also
    # unattached.
    assert route.unattached_documents == []


def test_venue_with_city_creates_new_stop_when_city_not_in_route() -> None:
    """A supplementary venue in a city the route doesn't have must CREATE."""
    route = WorkingRoute()
    update_route(
        route,
        _supplementary_fragment(
            source_id="sup-par-01",
            cities=["Paris"],
            venues=[
                {
                    "city": "Paris",
                    "kind": "sightseeing",
                    "identifier": "Eiffel Tower",
                }
            ],
        ),
    )

    assert len(route.stops) == 1
    stop = route.stops[0]
    assert stop.city == "Paris"
    assert len(stop.venues) == 1
    assert stop.venues[0].identifier == "Eiffel Tower"
    # Unanchored venue → stop has no arrival/departure projected from the
    # venue alone.
    assert stop.arrival_at is None
    assert stop.departure_at is None
    assert route.unattached_documents == []


# --------------------------------------------------------------------------- #
# Supplementary without place → unattached_documents
# --------------------------------------------------------------------------- #


def test_supplementary_without_place_lands_in_unattached_documents() -> None:
    """A supplementary fragment with empty stations / accommodations /
    venues becomes one :class:`UnattachedDocument` on the working route, and
    the route's ``stops`` / ``transits`` / id counters are untouched."""
    route = WorkingRoute()
    # Seed a prior stop so we can assert the supplementary doc didn't touch it.
    update_route(
        route,
        _hotel_fragment(
            source_id="hot-001",
            kind="hotel",
            city="Paris",
            identifier="Hotel Lutetia",
            check_in="2027-04-01T15:00:00Z",
            check_out="2027-04-03T11:00:00Z",
        ),
    )
    before_stops = [s.model_copy(deep=True) for s in route.stops]
    before_transits = list(route.transits)
    before_next_stop = route.next_stop_seq
    before_next_transit = route.next_transit_seq

    update_route(
        route,
        _supplementary_fragment(
            source_id="sup-park-01",
            prices=[{"amount": 12.50, "currency": "EUR"}],
            qr_codes=["QR-PAYLOAD"],
        ),
    )

    assert len(route.unattached_documents) == 1
    unattached = route.unattached_documents[0]
    assert unattached.source_document_id == "sup-park-01"
    assert unattached.document_type == "supplementary"
    assert unattached.prices == [Price(amount=12.50, currency="EUR")]
    assert unattached.qr_codes == ["QR-PAYLOAD"]

    # The route's identity surfaces are pristine.
    assert [(s.id, s.city) for s in route.stops] == [
        (s.id, s.city) for s in before_stops
    ]
    assert route.transits == before_transits
    assert route.next_stop_seq == before_next_stop
    assert route.next_transit_seq == before_next_transit


# --------------------------------------------------------------------------- #
# Op-level: AddUnattachedDocument is identity-clean
# --------------------------------------------------------------------------- #


def test_add_unattached_document_op_does_not_mutate_stops_or_counters() -> None:
    """Applying :class:`AddUnattachedDocument` must not touch ``stops`` /
    ``transits`` / ``next_stop_seq`` / ``next_transit_seq``."""
    route = WorkingRoute()
    update_route(
        route,
        _hotel_fragment(
            source_id="hot-001",
            kind="hotel",
            city="Paris",
            identifier="Hotel Lutetia",
            check_in="2027-04-01T15:00:00Z",
            check_out="2027-04-03T11:00:00Z",
        ),
    )
    snapshot_stops = [s.model_copy(deep=True) for s in route.stops]
    snapshot_transits = list(route.transits)
    snapshot_next_stop = route.next_stop_seq
    snapshot_next_transit = route.next_transit_seq

    op = AddUnattachedDocument(
        document=UnattachedDocument.model_validate(
            {
                "sourceDocumentId": "sup-x-01",
                "documentType": "supplementary",
            }
        )
    )
    apply(route, [op])

    assert len(route.unattached_documents) == 1
    assert [(s.id, s.city) for s in route.stops] == [
        (s.id, s.city) for s in snapshot_stops
    ]
    assert route.transits == snapshot_transits
    assert route.next_stop_seq == snapshot_next_stop
    assert route.next_transit_seq == snapshot_next_transit


# --------------------------------------------------------------------------- #
# Multi-venue same-city + duplicate dedupe
# --------------------------------------------------------------------------- #


def test_multi_venue_same_city_both_attach_to_one_stop() -> None:
    """Two distinct venues in the same city in one supplementary fragment
    must attach to the SAME stop (no duplicate stop created)."""
    route = WorkingRoute()
    update_route(
        route,
        _supplementary_fragment(
            source_id="sup-bcn-01",
            cities=["Barcelona"],
            venues=[
                {
                    "city": "Barcelona",
                    "kind": "sightseeing",
                    "identifier": "Sagrada Familia",
                },
                {
                    "city": "Barcelona",
                    "kind": "parking",
                    "identifier": "Plaza Catalunya Garage",
                },
            ],
        ),
    )

    assert len(route.stops) == 1
    stop = route.stops[0]
    assert stop.city == "Barcelona"
    venue_keys = [(v.kind, v.identifier) for v in stop.venues]
    assert ("sightseeing", "Sagrada Familia") in venue_keys
    assert ("parking", "Plaza Catalunya Garage") in venue_keys
    assert len(stop.venues) == 2


def test_malformed_repeated_venue_in_one_fragment_dedupes() -> None:
    """A malformed supplementary fragment repeating the same
    ``(kind, identifier)`` venue must result in only one attachment — the
    in-batch ledger's venue bucket prevents the duplicate from being
    attached twice."""
    route = WorkingRoute()
    update_route(
        route,
        _supplementary_fragment(
            source_id="sup-mal-01",
            cities=["Rome"],
            venues=[
                {
                    "city": "Rome",
                    "kind": "sightseeing",
                    "identifier": "Colosseum",
                    "validFromAt": "2027-06-01T09:00:00Z",
                },
                {
                    "city": "Rome",
                    "kind": "sightseeing",
                    "identifier": "Colosseum",
                    "validFromAt": "2027-06-01T09:00:00Z",
                },
            ],
        ),
    )

    assert len(route.stops) == 1
    stop = route.stops[0]
    venue_keys = [(v.kind, v.identifier) for v in stop.venues]
    assert venue_keys == [("sightseeing", "Colosseum")]


# --------------------------------------------------------------------------- #
# Scoring insensitivity
# --------------------------------------------------------------------------- #


def test_final_route_match_ignores_venues_and_unattached_documents() -> None:
    """A working route with venues attached to a stop and unattached
    documents on the route must match an expected route that has neither —
    confirming scoring's existing comparison keys never grew the new
    fields."""
    working = WorkingRoute(
        stops=[
            RouteStop.model_validate(
                {
                    "id": "stop-1",
                    "city": "Paris",
                    "arrivalAt": "2027-04-01T10:00:00Z",
                    "departureAt": "2027-04-03T12:00:00Z",
                    "travelers": ["traveler-1"],
                    "venues": [
                        {"kind": "sightseeing", "identifier": "Louvre"},
                    ],
                }
            ),
            RouteStop.model_validate(
                {
                    "id": "stop-2",
                    "city": "Lisbon",
                    "arrivalAt": "2027-04-03T15:00:00Z",
                    "travelers": ["traveler-1"],
                }
            ),
        ],
        transits=[
            Transit(
                id="transit-1",
                fromStopId="stop-1",
                toStopId="stop-2",
                mode=TransitMode.AIR,
                departureAt=_dt("2027-04-03T12:00:00Z"),
                arrivalAt=_dt("2027-04-03T15:00:00Z"),
                travelers=["traveler-1"],
                sourceFragmentId="frag-1",
            )
        ],
        next_stop_seq=3,
        next_transit_seq=2,
        unattachedDocuments=[
            UnattachedDocument.model_validate(
                {
                    "sourceDocumentId": "sup-x-01",
                    "documentType": "supplementary",
                }
            )
        ],
    )

    expected = ExpectedRoute.model_validate(
        {
            "travelers": ["traveler-1"],
            "stops": [
                {
                    "city": "Paris",
                    "arrivalAt": "2027-04-01T10:00:00Z",
                    "departureAt": "2027-04-03T12:00:00Z",
                    "travelers": ["traveler-1"],
                },
                {
                    "city": "Lisbon",
                    "arrivalAt": "2027-04-03T15:00:00Z",
                    "travelers": ["traveler-1"],
                },
            ],
            "transits": [
                {
                    "from": "Paris",
                    "to": "Lisbon",
                    "mode": "air",
                    "departureAt": "2027-04-03T12:00:00Z",
                    "arrivalAt": "2027-04-03T15:00:00Z",
                    "travelers": ["traveler-1"],
                    "sourceFragmentId": "frag-1",
                }
            ],
        }
    )

    result = final_route_match(working, expected)
    assert result.passed, result.reason
