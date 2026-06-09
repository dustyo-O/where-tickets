"""Offline tests for the integration trip-bundle generator (DUS-31 Slice 8).

Covers:

- Per-primitive: each builder produces a structurally-correct
  :class:`composer.PDFEntry` carrying valid :class:`ExtractedFields`.
- Composer end-to-end: the Phase-1 trip yields three PDFs + a three-stop
  expected-route + a manifest, each of which validates against its JSON
  schema.
- Determinism: two back-to-back :func:`compose_trip` calls on the same
  :class:`TripSpec` produce byte-identical JSON outputs.

These tests run in the persistent backend venv — no Bedrock, no live
extractor. The PDF generator's renderer is NOT exercised (WeasyPrint lives
in the ``corpus`` group); the renderer's contract is asserted through the
layer-2 PDF runner instead.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
PDF_SCHEMA_PATH = REPO_ROOT / "corpus" / "pdf" / "schema" / "expected-fields.schema.json"
ROUTE_SCHEMA_PATH = REPO_ROOT / "corpus" / "schema" / "expected-route.schema.json"


# --------------------------------------------------------------------------- #
# Schema helpers
# --------------------------------------------------------------------------- #


def _has_jsonschema() -> bool:
    try:
        import jsonschema  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


@pytest.fixture(scope="module")
def expected_fields_schema() -> dict[str, Any]:
    return json.loads(PDF_SCHEMA_PATH.read_text())


@pytest.fixture(scope="module")
def expected_route_schema() -> dict[str, Any]:
    return json.loads(ROUTE_SCHEMA_PATH.read_text())


def _validate(payload: dict[str, Any], schema: dict[str, Any]) -> None:
    """Validate ``payload`` against ``schema``; skip if jsonschema missing."""
    if not _has_jsonschema():
        pytest.skip("jsonschema not installed in this venv; schema check skipped")
    from jsonschema import Draft202012Validator  # noqa: PLC0415

    Draft202012Validator(schema).validate(payload)


# --------------------------------------------------------------------------- #
# Per-primitive expected-fields shape
# --------------------------------------------------------------------------- #


def test_air_leg_primitive_emits_two_stations() -> None:
    from corpus.integration.generator import primitives as p  # noqa: PLC0415

    leg = p.air_leg(
        from_city="Paris",
        to_city="Lisbon",
        from_identifier="CDG",
        to_identifier="LIS",
        departure_at=datetime(2027, 3, 11, 8, 30, tzinfo=UTC),
        arrival_at=datetime(2027, 3, 11, 10, 45, tzinfo=UTC),
        travelers=("Ines Marques",),
    )
    assert leg.mode == "air"
    assert leg.document_type == "air_ticket"
    assert leg.station_kind == "airport"
    assert len(leg.stations) == 2
    assert leg.stations[0].city == "Paris"
    assert leg.stations[1].city == "Lisbon"
    assert leg.cities == ("Paris", "Lisbon")


def test_hotel_stay_primitive_carries_kind_and_identifier() -> None:
    from corpus.integration.generator import primitives as p  # noqa: PLC0415

    stay = p.hotel_stay(
        city="Lisbon",
        identifier="Hotel Marques",
        check_in_at=datetime(2027, 3, 11, 15, 0, tzinfo=UTC),
        check_out_at=datetime(2027, 3, 13, 11, 0, tzinfo=UTC),
        travelers=("Ines Marques",),
    )
    assert stay.kind == "hotel"
    assert stay.document_type == "hotel_booking"
    assert stay.identifier == "Hotel Marques"


def test_supplementary_no_location_primitive_has_no_venue_anchor() -> None:
    from corpus.integration.generator import primitives as p  # noqa: PLC0415

    sup = p.supplementary_no_location(travelers=("Ines Marques",))
    assert sup.venue_kind is None
    assert sup.venue_city is None
    assert sup.document_type == "supplementary"


# --------------------------------------------------------------------------- #
# Composer end-to-end (Phase 1 trip)
# --------------------------------------------------------------------------- #


def _phase1_trip_spec() -> Any:
    """Build the Phase-1 trip spec inline to keep this test independent of
    :mod:`catalog`'s evolving public surface."""
    from corpus.integration.generator import primitives as p  # noqa: PLC0415
    from corpus.integration.generator.composer import TripSpec  # noqa: PLC0415

    travelers: tuple[str, ...] = ("Ines Marques",)
    return TripSpec(
        slug="01-air-out-hotel-back-paris-lisbon-1pax",
        travelers=travelers,
        notes="Phase 1 fixture.",
        primitives=(
            p.air_leg(
                from_city="Paris",
                to_city="Lisbon",
                from_identifier="CDG",
                to_identifier="LIS",
                departure_at=datetime(2027, 3, 11, 8, 30, tzinfo=UTC),
                arrival_at=datetime(2027, 3, 11, 10, 45, tzinfo=UTC),
                travelers=travelers,
            ),
            p.hotel_stay(
                city="Lisbon",
                identifier="Hotel Marques",
                check_in_at=datetime(2027, 3, 11, 15, 0, tzinfo=UTC),
                check_out_at=datetime(2027, 3, 15, 11, 0, tzinfo=UTC),
                travelers=travelers,
            ),
            p.air_leg(
                from_city="Lisbon",
                to_city="Paris",
                from_identifier="LIS",
                to_identifier="CDG",
                departure_at=datetime(2027, 3, 15, 18, 15, tzinfo=UTC),
                arrival_at=datetime(2027, 3, 15, 20, 30, tzinfo=UTC),
                travelers=travelers,
            ),
        ),
    )


def test_compose_trip_produces_three_pdfs_and_four_stop_route() -> None:
    """A 3-PDF "air → hotel → air-return" trip yields four stops, not three.

    The engine's accommodation sanity check (see
    :func:`spikes.route_engine_algorithmic.rules._sanity_check_would_invert`)
    splits the destination city into two stops: one for the inbound arrival,
    one for the hotel + outbound departure. The composer mirrors that rule.
    """
    from corpus.integration.generator.composer import compose_trip  # noqa: PLC0415

    bundle = compose_trip(_phase1_trip_spec())

    # Three PDFs in order.
    assert [entry.relpath for entry in bundle.pdfs] == [
        "01-air-out-hotel-back-paris-lisbon-1pax/01-air-leg-1.pdf",
        "01-air-out-hotel-back-paris-lisbon-1pax/02-hotel-1.pdf",
        "01-air-out-hotel-back-paris-lisbon-1pax/03-air-leg-2.pdf",
    ]
    # Four-stop route: Paris → Lisbon(arrival) → Lisbon(hotel+departure) → Paris.
    cities = [stop["city"] for stop in bundle.expected_route["stops"]]
    assert cities == ["Paris", "Lisbon", "Lisbon", "Paris"]
    # Two transits, both air.
    modes = [transit["mode"] for transit in bundle.expected_route["transits"]]
    assert modes == ["air", "air"]
    # The hotel attaches to the SECOND Lisbon stop.
    lisbon_arrival = bundle.expected_route["stops"][1]
    lisbon_hotel = bundle.expected_route["stops"][2]
    assert "accommodations" not in lisbon_arrival
    assert lisbon_hotel["accommodations"][0]["kind"] == "hotel"
    assert lisbon_hotel["accommodations"][0]["identifier"] == "Hotel Marques"
    # Manifest points at layer-2 paths.
    assert bundle.manifest["documents"][0]["pdf"].startswith("layer2/")
    assert bundle.manifest["travelers"] == ["Ines Marques"]


def test_compose_trip_expected_route_validates_against_schema(
    expected_route_schema: dict[str, Any],
) -> None:
    from corpus.integration.generator.composer import compose_trip  # noqa: PLC0415

    bundle = compose_trip(_phase1_trip_spec())
    _validate(bundle.expected_route, expected_route_schema)


def test_compose_trip_per_pdf_expected_fields_validate_against_schema(
    expected_fields_schema: dict[str, Any],
) -> None:
    from corpus.integration.generator.composer import compose_trip  # noqa: PLC0415

    bundle = compose_trip(_phase1_trip_spec())
    for entry in bundle.pdfs:
        if entry.expect_unreadable:
            continue
        assert entry.expected_fields is not None
        _validate(entry.expected_fields, expected_fields_schema)


def test_compose_trip_rejects_traveler_not_in_spec() -> None:
    from corpus.integration.generator import primitives as p  # noqa: PLC0415
    from corpus.integration.generator.composer import (  # noqa: PLC0415
        ComposerError,
        TripSpec,
        compose_trip,
    )

    spec = TripSpec(
        slug="bad",
        travelers=("Alice",),
        primitives=(
            p.air_leg(
                from_city="Paris",
                to_city="Lisbon",
                from_identifier="CDG",
                to_identifier="LIS",
                departure_at=datetime(2027, 3, 11, 8, 30, tzinfo=UTC),
                arrival_at=datetime(2027, 3, 11, 10, 45, tzinfo=UTC),
                travelers=("Bob",),
            ),
        ),
    )
    with pytest.raises(ComposerError):
        compose_trip(spec)


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


def test_compose_trip_is_byte_stable() -> None:
    from corpus.integration.generator.composer import compose_trip  # noqa: PLC0415

    spec = _phase1_trip_spec()
    bundle_a = compose_trip(spec)
    bundle_b = compose_trip(spec)

    def _canon(payload: dict[str, Any]) -> str:
        return json.dumps(payload, indent=2, sort_keys=True)

    assert _canon(bundle_a.manifest) == _canon(bundle_b.manifest)
    assert _canon(bundle_a.expected_route) == _canon(bundle_b.expected_route)
    assert len(bundle_a.pdfs) == len(bundle_b.pdfs)
    for entry_a, entry_b in zip(bundle_a.pdfs, bundle_b.pdfs, strict=True):
        assert entry_a.relpath == entry_b.relpath
        if entry_a.expected_fields is None or entry_b.expected_fields is None:
            assert entry_a.expected_fields is entry_b.expected_fields
            continue
        assert _canon(entry_a.expected_fields) == _canon(entry_b.expected_fields)
