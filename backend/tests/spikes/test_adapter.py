"""Tests for the :mod:`spikes.integration.adapter` ExtractedFields → Fragment mapping.

DUS-31 Slice 6. The adapter is a pure data mapping; this module pins:

- the per-variant fragment shape (transit / accommodation / supplementary)
  produced for each of the six document kinds the extractor emits;
- the snake_case → kebab-case ``document_type`` mapping;
- the treat-printed-as-UTC datetime convention (ISO local → tz-aware UTC,
  matching the engine corpus);
- the minimum-arity guards that turn malformed payloads into
  :class:`AdapterError` instead of leaking opaque ``ValidationError`` from
  deep inside the Pydantic fragment construction;
- the placeholder ``pnr`` / ``confirmation_code`` derivation from
  ``source_document_id``.

Adapter assertions (shape, datetimes, errors) run unconditionally on the
persistent backend venv. The schema-validation round-trip is gated per-fixture
on the optional ``jsonschema`` package (same dep group as
``tests/extraction/test_schema_contract.py``) — without it only those tests
skip; the rest continue to run.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from spikes.integration.adapter import (
    AdapterError,
    ExtractedFields,
    extracted_fields_to_fragment,
)
from spikes.route_engine_llm.models import (
    AccommodationFragment,
    SupplementaryFragment,
    TransitTicketFragment,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from jsonschema import (  # pyright: ignore[reportMissingModuleSource]
        Draft202012Validator,
    )


# Repo layout: backend/tests/spikes/test_adapter.py
#   parents[0] = tests/spikes/
#   parents[1] = tests/
#   parents[2] = backend/
#   parents[3] = <repo root>
_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "corpus"
    / "schema"
    / "extracted-fragment.schema.json"
)


@pytest.fixture(scope="module")
def fragment_schema() -> dict[str, Any]:
    """Load the extracted-fragment JSON Schema once per module."""
    return cast("dict[str, Any]", json.loads(_SCHEMA_PATH.read_text()))


@pytest.fixture(scope="module")
def fragment_validator(fragment_schema: dict[str, Any]) -> Draft202012Validator:
    """A reusable Draft 2020-12 validator bound to the fragment schema.

    ``jsonschema`` ships only with the optional ``extraction`` dep group; if
    it is absent we skip just the schema-validation tests, leaving the rest
    of the module running on the persistent backend venv.
    """
    jsonschema = pytest.importorskip("jsonschema")
    return cast(
        "Draft202012Validator", jsonschema.Draft202012Validator(fragment_schema)
    )


# --------------------------------------------------------------------------- #
# Builders — keep each test body focused on the assertion, not the dict shape.
# --------------------------------------------------------------------------- #


def _transit_fields(
    *,
    document_type: str,
    stations: list[dict[str, Any]],
    travelers: tuple[str, ...] = ("Alice",),
    cities: list[str] | None = None,
) -> ExtractedFields:
    """Build a transit-flavoured ``ExtractedFields`` payload."""
    payload: dict[str, Any] = {
        "document_type": document_type,
        "cities": cities if cities is not None else [],
        "stations": stations,
        "accommodations": [],
        "venues": [],
        "travelers": list(travelers),
        "prices": [],
        "qr_codes": [],
        "pdf_kind": "text",
    }
    return cast("ExtractedFields", payload)


def _accommodation_fields(
    *,
    document_type: str,
    accommodations: list[dict[str, Any]],
    cities: list[str],
    travelers: tuple[str, ...] = ("Alice",),
) -> ExtractedFields:
    """Build a hotel-/airbnb-booking flavoured ``ExtractedFields`` payload."""
    payload: dict[str, Any] = {
        "document_type": document_type,
        "cities": cities,
        "stations": [],
        "accommodations": accommodations,
        "venues": [],
        "travelers": list(travelers),
        "prices": [],
        "qr_codes": [],
        "pdf_kind": "text",
    }
    return cast("ExtractedFields", payload)


def _supplementary_fields(
    *,
    travelers: tuple[str, ...] = ("Alice",),
    cities: list[str] | None = None,
    stations: list[dict[str, Any]] | None = None,
    accommodations: list[dict[str, Any]] | None = None,
    venues: list[dict[str, Any]] | None = None,
    prices: list[dict[str, Any]] | None = None,
    qr_codes: list[str] | None = None,
) -> ExtractedFields:
    """Build a supplementary ``ExtractedFields`` payload."""
    payload: dict[str, Any] = {
        "document_type": "supplementary",
        "cities": cities if cities is not None else [],
        "stations": stations if stations is not None else [],
        "accommodations": accommodations if accommodations is not None else [],
        "venues": venues if venues is not None else [],
        "travelers": list(travelers),
        "prices": prices if prices is not None else [],
        "qr_codes": qr_codes if qr_codes is not None else [],
        "pdf_kind": "text",
    }
    return cast("ExtractedFields", payload)


# --------------------------------------------------------------------------- #
# Transit fragments: air / rail / bus
# --------------------------------------------------------------------------- #


def test_air_ticket_single_leg_single_traveler() -> None:
    """One air ticket, one traveler, two stations → ``TransitTicketFragment``."""
    fields = _transit_fields(
        document_type="air_ticket",
        cities=["Paris", "Lisbon"],
        stations=[
            {
                "city": "Paris",
                "kind": "airport",
                "identifier": "CDG",
                "departure_datetime": "2027-03-11T08:30:00",
            },
            {
                "city": "Lisbon",
                "kind": "airport",
                "identifier": "LIS",
                "arrival_datetime": "2027-03-11T10:45:00",
            },
        ],
        travelers=("Pierre Dubois",),
    )

    fragment = extracted_fields_to_fragment(fields, source_document_id="doc-air-01")

    assert isinstance(fragment, TransitTicketFragment)
    assert fragment.document_type == "air-ticket"
    assert fragment.source_document_id == "doc-air-01"
    assert fragment.pnr == "doc-air-01"
    assert fragment.travelers == ["Pierre Dubois"]
    assert fragment.cities == ["Paris", "Lisbon"]
    assert len(fragment.stations) == 2
    assert fragment.stations[0].city == "Paris"
    assert fragment.stations[0].kind == "airport"
    assert fragment.stations[0].departure_at == datetime(
        2027, 3, 11, 8, 30, tzinfo=UTC
    )
    assert fragment.stations[0].arrival_at is None
    assert fragment.stations[1].departure_at is None
    assert fragment.stations[1].arrival_at == datetime(
        2027, 3, 11, 10, 45, tzinfo=UTC
    )


def test_air_ticket_return_single_traveler() -> None:
    """A return-trip air ticket compacts the turnaround into one layover station."""
    fields = _transit_fields(
        document_type="air_ticket",
        cities=["Paris", "Lisbon"],
        stations=[
            {
                "city": "Paris",
                "kind": "airport",
                "identifier": "CDG",
                "departure_datetime": "2027-03-11T08:30:00",
            },
            {
                "city": "Lisbon",
                "kind": "airport",
                "identifier": "LIS",
                "arrival_datetime": "2027-03-11T10:45:00",
                "departure_datetime": "2027-03-15T18:00:00",
            },
            {
                "city": "Paris",
                "kind": "airport",
                "identifier": "CDG",
                "arrival_datetime": "2027-03-15T20:30:00",
            },
        ],
        travelers=("Pierre Dubois",),
    )

    fragment = extracted_fields_to_fragment(fields, source_document_id="doc-air-rt")

    assert isinstance(fragment, TransitTicketFragment)
    assert fragment.document_type == "air-ticket"
    assert len(fragment.stations) == 3
    turnaround = fragment.stations[1]
    assert turnaround.city == "Lisbon"
    assert turnaround.arrival_at == datetime(2027, 3, 11, 10, 45, tzinfo=UTC)
    assert turnaround.departure_at == datetime(2027, 3, 15, 18, 0, tzinfo=UTC)


def test_rail_ticket_multi_leg_layover() -> None:
    """A 3-station rail ticket A→B→C carries a B layover with both datetimes."""
    fields = _transit_fields(
        document_type="rail_ticket",
        cities=["Berlin", "Munich", "Vienna"],
        stations=[
            {
                "city": "Berlin",
                "kind": "rail_station",
                "identifier": "Berlin Hbf",
                "departure_datetime": "2027-04-02T07:00:00",
            },
            {
                "city": "Munich",
                "kind": "rail_station",
                "identifier": "Munich Hbf",
                "arrival_datetime": "2027-04-02T11:30:00",
                "departure_datetime": "2027-04-02T12:15:00",
            },
            {
                "city": "Vienna",
                "kind": "rail_station",
                "identifier": "Wien Hbf",
                "arrival_datetime": "2027-04-02T16:45:00",
            },
        ],
    )

    fragment = extracted_fields_to_fragment(fields, source_document_id="doc-rail-01")

    assert isinstance(fragment, TransitTicketFragment)
    assert fragment.document_type == "rail-ticket"
    assert len(fragment.stations) == 3
    assert all(s.kind == "rail_station" for s in fragment.stations)
    middle = fragment.stations[1]
    assert middle.arrival_at == datetime(2027, 4, 2, 11, 30, tzinfo=UTC)
    assert middle.departure_at == datetime(2027, 4, 2, 12, 15, tzinfo=UTC)


def test_bus_ticket_multi_traveler() -> None:
    """A bus ticket carries every traveler through to the fragment."""
    fields = _transit_fields(
        document_type="bus_ticket",
        cities=["Madrid", "Barcelona"],
        stations=[
            {
                "city": "Madrid",
                "kind": "bus_terminal",
                "identifier": "Madrid Sur",
                "departure_datetime": "2027-05-10T09:00:00",
            },
            {
                "city": "Barcelona",
                "kind": "bus_terminal",
                "identifier": "Barcelona Nord",
                "arrival_datetime": "2027-05-10T16:00:00",
            },
        ],
        travelers=("Alice", "Bob"),
    )

    fragment = extracted_fields_to_fragment(fields, source_document_id="doc-bus-01")

    assert isinstance(fragment, TransitTicketFragment)
    assert fragment.document_type == "bus-ticket"
    assert fragment.travelers == ["Alice", "Bob"]
    assert len(fragment.stations) == 2
    assert all(s.kind == "bus_terminal" for s in fragment.stations)


# --------------------------------------------------------------------------- #
# Accommodation fragments: hotel / airbnb
# --------------------------------------------------------------------------- #


def test_hotel_booking_single_accommodation() -> None:
    """One hotel entry → :class:`AccommodationFragment` with ``hotel-booking``."""
    fields = _accommodation_fields(
        document_type="hotel_booking",
        cities=["Lisbon"],
        accommodations=[
            {
                "city": "Lisbon",
                "kind": "hotel",
                "identifier": "Hotel Lutetia",
                "check_in_datetime": "2027-06-01T15:00:00",
                "check_out_datetime": "2027-06-03T11:00:00",
            }
        ],
    )

    fragment = extracted_fields_to_fragment(fields, source_document_id="doc-hotel-01")

    assert isinstance(fragment, AccommodationFragment)
    assert fragment.document_type == "hotel-booking"
    assert fragment.source_document_id == "doc-hotel-01"
    assert fragment.confirmation_code == "doc-hotel-01"
    assert len(fragment.accommodations) == 1
    entry = fragment.accommodations[0]
    assert entry.city == "Lisbon"
    assert entry.kind == "hotel"
    assert entry.identifier == "Hotel Lutetia"
    assert entry.check_in_at == datetime(2027, 6, 1, 15, 0, tzinfo=UTC)
    assert entry.check_out_at == datetime(2027, 6, 3, 11, 0, tzinfo=UTC)


def test_airbnb_booking_single_accommodation() -> None:
    """One airbnb entry → :class:`AccommodationFragment` with ``airbnb-booking``."""
    fields = _accommodation_fields(
        document_type="airbnb_booking",
        cities=["Lisbon"],
        accommodations=[
            {
                "city": "Lisbon",
                "kind": "airbnb",
                "identifier": "Airbnb - Loft in Alfama",
                "check_in_datetime": "2027-07-04T16:00:00",
                "check_out_datetime": "2027-07-07T10:00:00",
            }
        ],
    )

    fragment = extracted_fields_to_fragment(
        fields, source_document_id="doc-airbnb-01"
    )

    assert isinstance(fragment, AccommodationFragment)
    assert fragment.document_type == "airbnb-booking"
    assert fragment.confirmation_code == "doc-airbnb-01"
    entry = fragment.accommodations[0]
    assert entry.kind == "airbnb"
    assert entry.identifier == "Airbnb - Loft in Alfama"
    assert entry.check_in_at == datetime(2027, 7, 4, 16, 0, tzinfo=UTC)
    assert entry.check_out_at == datetime(2027, 7, 7, 10, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Supplementary fragments
# --------------------------------------------------------------------------- #


def test_supplementary_with_venue() -> None:
    """A sightseeing voucher → :class:`SupplementaryFragment` with one venue."""
    fields = _supplementary_fields(
        cities=["Lisbon"],
        venues=[
            {
                "city": "Lisbon",
                "kind": "sightseeing",
                "identifier": "Jeronimos Monastery",
                "valid_from_datetime": "2027-06-02T10:00:00",
            }
        ],
    )

    fragment = extracted_fields_to_fragment(fields, source_document_id="doc-supp-01")

    assert isinstance(fragment, SupplementaryFragment)
    assert fragment.document_type == "supplementary"
    assert fragment.cities == ["Lisbon"]
    assert len(fragment.venues) == 1
    venue = fragment.venues[0]
    assert venue.city == "Lisbon"
    assert venue.kind == "sightseeing"
    assert venue.identifier == "Jeronimos Monastery"
    assert venue.valid_from_at == datetime(2027, 6, 2, 10, 0, tzinfo=UTC)
    assert venue.valid_to_at is None


def test_supplementary_without_routable_place() -> None:
    """A placeless supplementary carries empty routable lists through.

    Slice 5's rules turn this into an ``UnattachedDocument`` at the engine
    boundary; the adapter just preserves the shape.
    """
    fields = _supplementary_fields()

    fragment = extracted_fields_to_fragment(fields, source_document_id="doc-supp-02")

    assert isinstance(fragment, SupplementaryFragment)
    assert fragment.document_type == "supplementary"
    assert fragment.travelers == ["Alice"]
    assert fragment.cities == []
    assert fragment.stations == []
    assert fragment.accommodations == []
    assert fragment.venues == []
    assert fragment.prices == []
    assert fragment.qr_codes == []


# --------------------------------------------------------------------------- #
# Error paths
# --------------------------------------------------------------------------- #


def test_supplementary_missing_travelers_raises_adapter_error() -> None:
    """A supplementary without travelers is rejected at the adapter boundary."""
    fields = _supplementary_fields(travelers=())

    with pytest.raises(AdapterError, match=r"supplementary document .* >=1 traveler"):
        extracted_fields_to_fragment(fields, source_document_id="doc-supp-bad")


def test_transit_with_single_station_raises_adapter_error() -> None:
    """A transit ticket with fewer than two stations is rejected."""
    fields = _transit_fields(
        document_type="air_ticket",
        cities=["Paris"],
        stations=[
            {
                "city": "Paris",
                "kind": "airport",
                "identifier": "CDG",
                "departure_datetime": "2027-03-11T08:30:00",
            },
        ],
    )

    with pytest.raises(AdapterError, match=r"transit ticket .* >=2 stations"):
        extracted_fields_to_fragment(fields, source_document_id="doc-air-bad")


def test_unknown_document_type_raises_adapter_error() -> None:
    """An out-of-enum ``document_type`` value surfaces as :class:`AdapterError`."""
    payload: dict[str, Any] = {
        "document_type": "foo_ticket",  # not in the extractor enum
        "cities": [],
        "stations": [],
        "accommodations": [],
        "venues": [],
        "travelers": ["Alice"],
        "prices": [],
        "qr_codes": [],
        "pdf_kind": "text",
    }
    fields = cast("ExtractedFields", payload)

    with pytest.raises(AdapterError, match="unknown document_type"):
        extracted_fields_to_fragment(fields, source_document_id="doc-unknown")


# --------------------------------------------------------------------------- #
# Schema-validation round-trip (gated on jsonschema at module top)
# --------------------------------------------------------------------------- #


def _round_trip_payload(
    fields: ExtractedFields, *, source_document_id: str
) -> dict[str, Any]:
    """Adapt + JSON-dump a fragment in alias form (what the schema validates).

    ``exclude_none=True`` mirrors the on-disk fragment fixtures under
    ``corpus/scenarios/*/fragments/*.json`` — the schema's optional fields
    accept the property being absent, not present-and-``null``.
    """
    fragment = extracted_fields_to_fragment(
        fields, source_document_id=source_document_id
    )
    return cast(
        "dict[str, Any]",
        fragment.model_dump(by_alias=True, mode="json", exclude_none=True),
    )


@pytest.mark.parametrize(
    "case_id",
    [
        "air-single",
        "air-return",
        "rail-layover",
        "bus-multi-traveler",
        "hotel",
        "airbnb",
        "supplementary-venue",
        "supplementary-empty",
    ],
)
def test_fragment_validates_against_schema(
    case_id: str, fragment_validator: Draft202012Validator
) -> None:
    """Every successful adapter output validates against the fragment schema."""
    fields, source_document_id = _CASES[case_id]
    payload = _round_trip_payload(fields, source_document_id=source_document_id)
    errors = sorted(
        fragment_validator.iter_errors(payload), key=lambda e: list(e.absolute_path)
    )
    assert errors == [], [
        f"{list(e.absolute_path) or ['<root>']}: {e.message}" for e in errors
    ]


# Inputs reused by the parametrized schema round-trip. Kept module-level so the
# parametrize ids stay declarative and the inputs are visible next to the test.
_CASES: dict[str, tuple[ExtractedFields, str]] = {
    "air-single": (
        _transit_fields(
            document_type="air_ticket",
            cities=["Paris", "Lisbon"],
            stations=[
                {
                    "city": "Paris",
                    "kind": "airport",
                    "identifier": "CDG",
                    "departure_datetime": "2027-03-11T08:30:00",
                },
                {
                    "city": "Lisbon",
                    "kind": "airport",
                    "identifier": "LIS",
                    "arrival_datetime": "2027-03-11T10:45:00",
                },
            ],
            travelers=("Pierre Dubois",),
        ),
        "doc-air-01",
    ),
    "air-return": (
        _transit_fields(
            document_type="air_ticket",
            cities=["Paris", "Lisbon"],
            stations=[
                {
                    "city": "Paris",
                    "kind": "airport",
                    "identifier": "CDG",
                    "departure_datetime": "2027-03-11T08:30:00",
                },
                {
                    "city": "Lisbon",
                    "kind": "airport",
                    "identifier": "LIS",
                    "arrival_datetime": "2027-03-11T10:45:00",
                    "departure_datetime": "2027-03-15T18:00:00",
                },
                {
                    "city": "Paris",
                    "kind": "airport",
                    "identifier": "CDG",
                    "arrival_datetime": "2027-03-15T20:30:00",
                },
            ],
            travelers=("Pierre Dubois",),
        ),
        "doc-air-rt",
    ),
    "rail-layover": (
        _transit_fields(
            document_type="rail_ticket",
            cities=["Berlin", "Munich", "Vienna"],
            stations=[
                {
                    "city": "Berlin",
                    "kind": "rail_station",
                    "identifier": "Berlin Hbf",
                    "departure_datetime": "2027-04-02T07:00:00",
                },
                {
                    "city": "Munich",
                    "kind": "rail_station",
                    "identifier": "Munich Hbf",
                    "arrival_datetime": "2027-04-02T11:30:00",
                    "departure_datetime": "2027-04-02T12:15:00",
                },
                {
                    "city": "Vienna",
                    "kind": "rail_station",
                    "identifier": "Wien Hbf",
                    "arrival_datetime": "2027-04-02T16:45:00",
                },
            ],
        ),
        "doc-rail-01",
    ),
    "bus-multi-traveler": (
        _transit_fields(
            document_type="bus_ticket",
            cities=["Madrid", "Barcelona"],
            stations=[
                {
                    "city": "Madrid",
                    "kind": "bus_terminal",
                    "identifier": "Madrid Sur",
                    "departure_datetime": "2027-05-10T09:00:00",
                },
                {
                    "city": "Barcelona",
                    "kind": "bus_terminal",
                    "identifier": "Barcelona Nord",
                    "arrival_datetime": "2027-05-10T16:00:00",
                },
            ],
            travelers=("Alice", "Bob"),
        ),
        "doc-bus-01",
    ),
    "hotel": (
        _accommodation_fields(
            document_type="hotel_booking",
            cities=["Lisbon"],
            accommodations=[
                {
                    "city": "Lisbon",
                    "kind": "hotel",
                    "identifier": "Hotel Lutetia",
                    "check_in_datetime": "2027-06-01T15:00:00",
                    "check_out_datetime": "2027-06-03T11:00:00",
                }
            ],
        ),
        "doc-hotel-01",
    ),
    "airbnb": (
        _accommodation_fields(
            document_type="airbnb_booking",
            cities=["Lisbon"],
            accommodations=[
                {
                    "city": "Lisbon",
                    "kind": "airbnb",
                    "identifier": "Airbnb - Loft in Alfama",
                    "check_in_datetime": "2027-07-04T16:00:00",
                    "check_out_datetime": "2027-07-07T10:00:00",
                }
            ],
        ),
        "doc-airbnb-01",
    ),
    "supplementary-venue": (
        _supplementary_fields(
            cities=["Lisbon"],
            venues=[
                {
                    "city": "Lisbon",
                    "kind": "sightseeing",
                    "identifier": "Jeronimos Monastery",
                    "valid_from_datetime": "2027-06-02T10:00:00",
                }
            ],
        ),
        "doc-supp-01",
    ),
    "supplementary-empty": (
        _supplementary_fields(),
        "doc-supp-02",
    ),
}


# --------------------------------------------------------------------------- #
# Determinism + price round-trip
# --------------------------------------------------------------------------- #


def test_datetime_conversion_is_treat_printed_as_utc() -> None:
    """An ISO-local printed time becomes the same wall-clock UTC datetime."""
    fields = _transit_fields(
        document_type="air_ticket",
        cities=["Paris", "Lisbon"],
        stations=[
            {
                "city": "Paris",
                "kind": "airport",
                "identifier": "CDG",
                "departure_datetime": "2027-03-11T08:30:00",
            },
            {
                "city": "Lisbon",
                "kind": "airport",
                "identifier": "LIS",
                "arrival_datetime": "2027-03-11T10:45:00",
            },
        ],
    )

    fragment = extracted_fields_to_fragment(fields, source_document_id="doc-dt-01")

    assert isinstance(fragment, TransitTicketFragment)
    assert fragment.stations[0].departure_at == datetime(
        2027, 3, 11, 8, 30, 0, tzinfo=UTC
    )


def test_supplementary_prices_round_trip() -> None:
    """A supplementary's ``prices[]`` is carried through, including the empty case."""
    fields = _supplementary_fields(prices=[{"amount": 12.5, "currency": "EUR"}])

    fragment = extracted_fields_to_fragment(fields, source_document_id="doc-supp-pr")

    assert isinstance(fragment, SupplementaryFragment)
    assert len(fragment.prices) == 1
    assert fragment.prices[0].amount == 12.5
    assert fragment.prices[0].currency == "EUR"

    empty_fields = _supplementary_fields()
    empty_fragment = extracted_fields_to_fragment(
        empty_fields, source_document_id="doc-supp-pr-empty"
    )
    assert isinstance(empty_fragment, SupplementaryFragment)
    assert empty_fragment.prices == []
