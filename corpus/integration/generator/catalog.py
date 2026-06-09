"""Curated catalog of integration trips.

Phase 1 (DUS-31 Slice 8): one trip end-to-end.
Phase 2 extends this to ~20-25 trips covering every dimension in spec
007 §2.7.

Each public ``build_<slug>()`` function returns a :class:`composer.TripSpec`.
:func:`all_trips` exposes them as an ordered list keyed by slug — the CLI
iterates this when no ``--trip`` filter is given.

Determinism: each trip is described inline with constant cities, datetimes,
travelers, prices, and identifiers. The composer adds nothing random on top
(per-PDF noise is seeded from the scenario id, which is itself stable from
the trip slug). Two regen runs produce byte-identical manifest / expected
route / per-PDF expected fields.
"""

from __future__ import annotations

from datetime import UTC, datetime

from corpus.integration.generator import primitives as p
from corpus.integration.generator.composer import TripSpec

__all__ = ["all_trips", "build_01_air_out_hotel_back_paris_lisbon_1pax"]


def _dt(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    """Tiny helper — UTC tz-aware datetime in one line."""
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# 01 — air out + Paris hotel ... no, wait: the slug says
#      "air-out-hotel-back-paris-lisbon-1pax". The trip is:
#        outbound air Paris -> Lisbon
#        hotel in Lisbon
#        return air Lisbon -> Paris
# --------------------------------------------------------------------------- #


def build_01_air_out_hotel_back_paris_lisbon_1pax() -> TripSpec:
    """First generated trip — 3 PDFs, 1 traveler, Paris -> Lisbon -> Paris.

    Doc shapes chosen to land in the extractor's reliable zone:

    - One-way air ticket (2 stations) — layer-1 has many passing examples.
    - Hotel booking (one accommodation entry) — same.
    - One-way return air ticket (2 stations) — same.
    """
    traveler = "Ines Marques"
    travelers: tuple[str, ...] = (traveler,)
    return TripSpec(
        slug="01-air-out-hotel-back-paris-lisbon-1pax",
        travelers=travelers,
        notes=(
            "Air out (Paris CDG -> Lisbon LIS), hotel in Lisbon, "
            "air back (Lisbon LIS -> Paris CDG). One traveler. "
            "Tests the integration runner against a 3-PDF chained trip."
        ),
        primitives=(
            p.air_leg(
                from_city="Paris",
                to_city="Lisbon",
                from_identifier="CDG",
                to_identifier="LIS",
                departure_at=_dt(2027, 3, 11, 8, 30),
                arrival_at=_dt(2027, 3, 11, 10, 45),
                travelers=travelers,
                price_eur=149.50,
                pnr="PARLISOUT",
            ),
            p.hotel_stay(
                city="Lisbon",
                identifier="Hotel Marques de Pombal",
                check_in_at=_dt(2027, 3, 11, 15, 0),
                check_out_at=_dt(2027, 3, 15, 11, 0),
                travelers=travelers,
                price_eur=620.00,
                confirmation_code="HTL-LISBON-001",
            ),
            p.air_leg(
                from_city="Lisbon",
                to_city="Paris",
                from_identifier="LIS",
                to_identifier="CDG",
                departure_at=_dt(2027, 3, 15, 18, 15),
                arrival_at=_dt(2027, 3, 15, 20, 30),
                travelers=travelers,
                price_eur=159.00,
                pnr="LISPARRET",
            ),
        ),
    )


def all_trips() -> list[TripSpec]:
    """Return every trip in the catalogue, in slug order."""
    return [
        build_01_air_out_hotel_back_paris_lisbon_1pax(),
    ]
