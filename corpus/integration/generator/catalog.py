"""Curated catalog of integration trips.

DUS-31 Slice 8 phase 2: ~20+ trips covering every dimension in functional
spec 007 §2.7:

- per-mode straight: air, rail, bus, mixed-mode 3-city
- per-mode return: air, rail, bus
- multi-traveler (2pax) trips
- airbnb (vs hotel)
- same-city two-airports collapse
- divergent travelers mid-trip
- supplementary-with-venue + supplementary-no-location
- scan-PDF mix (rasterized)
- unreadable PDF (extractor failure expected)
- "real-shape" itineraries with mixed modes / cities / accommodations / venues

Each public ``build_*()`` returns a :class:`composer.TripSpec`. :func:`all_trips`
exposes the catalog in slug order. Determinism: every trip is described inline
with constant cities, datetimes, travelers, prices, and identifiers; the
composer's per-PDF noise is seeded from the scenario id which is itself stable
from the trip slug.
"""

from __future__ import annotations

from datetime import UTC, datetime

from corpus.integration.generator import primitives as p
from corpus.integration.generator.composer import TripSpec

__all__ = ["all_trips"]


def _dt(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    """UTC tz-aware datetime in one line."""
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


# Shared traveler list for the 1pax base
_INES: tuple[str, ...] = ("Ines Marques",)
_INES_LUCAS: tuple[str, ...] = ("Ines Marques", "Lucas Costa")
_INES_MARTA: tuple[str, ...] = ("Ines Marques", "Marta Kowalski")


# --------------------------------------------------------------------------- #
# 01 — air out + hotel + air back (Phase 1 fixture)
# --------------------------------------------------------------------------- #


def _build_01_air_out_hotel_back_paris_lisbon_1pax() -> TripSpec:
    return TripSpec(
        slug="01-air-out-hotel-back-paris-lisbon-1pax",
        travelers=_INES,
        notes="Air out + Lisbon hotel + air back, 1 traveler.",
        primitives=(
            p.air_leg(
                from_city="Paris", to_city="Lisbon",
                from_identifier="CDG", to_identifier="LIS",
                departure_at=_dt(2027, 3, 11, 8, 30),
                arrival_at=_dt(2027, 3, 11, 10, 45),
                travelers=_INES, price_eur=149.50, pnr="PARLISOUT",
            ),
            p.hotel_stay(
                city="Lisbon", identifier="Hotel Marques de Pombal",
                check_in_at=_dt(2027, 3, 11, 15, 0),
                check_out_at=_dt(2027, 3, 15, 11, 0),
                travelers=_INES, price_eur=620.00, confirmation_code="HTL-LIS-001",
            ),
            p.air_leg(
                from_city="Lisbon", to_city="Paris",
                from_identifier="LIS", to_identifier="CDG",
                departure_at=_dt(2027, 3, 15, 18, 15),
                arrival_at=_dt(2027, 3, 15, 20, 30),
                travelers=_INES, price_eur=159.00, pnr="LISPARRET",
            ),
        ),
    )


# --------------------------------------------------------------------------- #
# 02 — single-PDF air-return (proves the runner handles single-PDF trips)
# --------------------------------------------------------------------------- #


def _build_02_air_return_1pax_frankfurt_amsterdam() -> TripSpec:
    return TripSpec(
        slug="02-air-return-1pax-frankfurt-amsterdam",
        travelers=_INES,
        notes="Single-PDF compact-form return air, 1 traveler. Proves the runner handles single-PDF trips.",
        primitives=(
            p.air_return(
                from_city="Frankfurt", to_city="Amsterdam",
                from_identifier="FRA", to_identifier="AMS",
                outbound_departure_at=_dt(2027, 4, 1, 9, 0),
                outbound_arrival_at=_dt(2027, 4, 1, 10, 30),
                return_departure_at=_dt(2027, 4, 5, 17, 0),
                return_arrival_at=_dt(2027, 4, 5, 18, 30),
                travelers=_INES, price_eur=224.00, pnr="FRAAMSRT",
            ),
        ),
    )


# --------------------------------------------------------------------------- #
# 03 — rail out + hotel + rail back (per-mode rail)
# --------------------------------------------------------------------------- #


def _build_03_rail_out_hotel_back_1pax_paris_lisbon() -> TripSpec:
    return TripSpec(
        slug="03-rail-out-hotel-back-1pax-paris-lisbon",
        travelers=_INES,
        notes="Rail out + Lisbon hotel + rail back, 1 traveler.",
        primitives=(
            p.rail_leg(
                from_city="Paris", to_city="Lisbon",
                from_identifier="Paris Gare du Nord", to_identifier="Lisboa Oriente",
                departure_at=_dt(2027, 5, 3, 7, 45),
                arrival_at=_dt(2027, 5, 3, 10, 55),
                travelers=_INES, price_eur=129.00, pnr="RAILPARLIS",
            ),
            p.hotel_stay(
                city="Lisbon", identifier="Hotel Saint-Cloud Rive Gauche",
                check_in_at=_dt(2027, 5, 3, 15, 0),
                check_out_at=_dt(2027, 5, 6, 11, 0),
                travelers=_INES, price_eur=380.00, confirmation_code="HTL-LIS-003",
            ),
            p.rail_leg(
                from_city="Lisbon", to_city="Paris",
                from_identifier="Lisboa Oriente", to_identifier="Paris Gare du Nord",
                departure_at=_dt(2027, 5, 6, 17, 20),
                arrival_at=_dt(2027, 5, 6, 20, 30),
                travelers=_INES, price_eur=135.00, pnr="RAILLISPAR",
            ),
        ),
    )


# --------------------------------------------------------------------------- #
# 04 — single-PDF bus-return (per-mode bus)
# --------------------------------------------------------------------------- #


def _build_04_bus_return_1pax_madrid_rome() -> TripSpec:
    return TripSpec(
        slug="04-bus-return-1pax-madrid-rome",
        travelers=_INES,
        notes="Single-PDF bus return, 1 traveler.",
        primitives=(
            p.bus_return(
                from_city="Madrid", to_city="Rome",
                from_identifier="Madrid Mendez Alvaro", to_identifier="Roma Tiburtina Bus",
                outbound_departure_at=_dt(2027, 6, 2, 6, 15),
                outbound_arrival_at=_dt(2027, 6, 2, 12, 45),
                return_departure_at=_dt(2027, 6, 6, 15, 45),
                return_arrival_at=_dt(2027, 6, 6, 22, 15),
                travelers=_INES, price_eur=78.00, pnr="BUSMADROM",
            ),
        ),
    )


# --------------------------------------------------------------------------- #
# 05 — mixed-mode 3-city straight: air A→B + rail B→C
# --------------------------------------------------------------------------- #


def _build_05_air_rail_3city_1pax_paris_frankfurt_amsterdam() -> TripSpec:
    return TripSpec(
        slug="05-air-rail-3city-1pax-paris-frankfurt-amsterdam",
        travelers=_INES,
        notes="3-city straight: air Paris→Frankfurt, rail Frankfurt→Amsterdam, 1 traveler.",
        primitives=(
            p.air_leg(
                from_city="Paris", to_city="Frankfurt",
                from_identifier="CDG", to_identifier="FRA",
                departure_at=_dt(2027, 7, 1, 7, 30),
                arrival_at=_dt(2027, 7, 1, 9, 0),
                travelers=_INES, price_eur=139.00, pnr="PARFRAAIR",
            ),
            p.rail_leg(
                from_city="Frankfurt", to_city="Amsterdam",
                from_identifier="Frankfurt Hauptbahnhof", to_identifier="Amsterdam Centraal",
                departure_at=_dt(2027, 7, 2, 8, 15),
                arrival_at=_dt(2027, 7, 2, 12, 25),
                travelers=_INES, price_eur=89.00, pnr="FRAAMSRAIL",
            ),
        ),
    )


# --------------------------------------------------------------------------- #
# 06 — air straight 2pax (multi-traveler)
# --------------------------------------------------------------------------- #


def _build_06_air_straight_2pax_madrid_rome() -> TripSpec:
    return TripSpec(
        slug="06-air-straight-2pax-madrid-rome",
        travelers=_INES_LUCAS,
        notes="Single air leg, 2 travelers.",
        primitives=(
            p.air_leg(
                from_city="Madrid", to_city="Rome",
                from_identifier="MAD", to_identifier="FCO",
                departure_at=_dt(2027, 8, 1, 10, 0),
                arrival_at=_dt(2027, 8, 1, 12, 30),
                travelers=_INES_LUCAS, price_eur=298.00, pnr="MADROM2PAX",
            ),
        ),
    )


# --------------------------------------------------------------------------- #
# 07 — air out + hotel + air back 2pax (multi-traveler with accommodation)
# --------------------------------------------------------------------------- #


def _build_07_air_out_hotel_back_2pax_london_vienna() -> TripSpec:
    return TripSpec(
        slug="07-air-out-hotel-back-2pax-london-vienna",
        travelers=_INES_LUCAS,
        notes="Air out + Vienna hotel + air back, 2 travelers.",
        primitives=(
            p.air_leg(
                from_city="London", to_city="Vienna",
                from_identifier="LHR", to_identifier="VIE",
                departure_at=_dt(2027, 9, 5, 8, 0),
                arrival_at=_dt(2027, 9, 5, 11, 30),
                travelers=_INES_LUCAS, price_eur=320.00, pnr="LHRVIEOUT",
            ),
            p.hotel_stay(
                city="Vienna", identifier="Hotel Kensington Garden Court",
                check_in_at=_dt(2027, 9, 5, 15, 0),
                check_out_at=_dt(2027, 9, 8, 11, 0),
                travelers=_INES_LUCAS, price_eur=510.00, confirmation_code="HTL-VIE-007",
            ),
            p.air_leg(
                from_city="Vienna", to_city="London",
                from_identifier="VIE", to_identifier="LHR",
                departure_at=_dt(2027, 9, 8, 16, 30),
                arrival_at=_dt(2027, 9, 8, 18, 30),
                travelers=_INES_LUCAS, price_eur=305.00, pnr="VIELHRRET",
            ),
        ),
    )


# --------------------------------------------------------------------------- #
# 08 — air out + airbnb + air back, 1pax (airbnb variant)
# --------------------------------------------------------------------------- #


def _build_08_air_out_airbnb_back_1pax_berlin_prague() -> TripSpec:
    return TripSpec(
        slug="08-air-out-airbnb-back-1pax-berlin-prague",
        travelers=_INES,
        notes="Air out + Prague Airbnb + air back, 1 traveler.",
        primitives=(
            p.air_leg(
                from_city="Berlin", to_city="Prague",
                from_identifier="BER", to_identifier="PRG",
                departure_at=_dt(2027, 10, 1, 9, 15),
                arrival_at=_dt(2027, 10, 1, 10, 30),
                travelers=_INES, price_eur=110.00, pnr="BERPRGAIR",
            ),
            p.airbnb_stay(
                city="Prague", identifier="Charming Flat in Zizkov",
                check_in_at=_dt(2027, 10, 1, 15, 0),
                check_out_at=_dt(2027, 10, 4, 11, 0),
                travelers=_INES, price_eur=240.00, confirmation_code="ABN-PRG-008",
            ),
            p.air_leg(
                from_city="Prague", to_city="Berlin",
                from_identifier="PRG", to_identifier="BER",
                departure_at=_dt(2027, 10, 4, 17, 0),
                arrival_at=_dt(2027, 10, 4, 18, 15),
                travelers=_INES, price_eur=115.00, pnr="PRGBERAIR",
            ),
        ),
    )


# --------------------------------------------------------------------------- #
# 09 — same-city two-airports collapse: Paris (CDG) → Lisbon, Lisbon → Paris (ORY)
# --------------------------------------------------------------------------- #


def _build_09_same_city_two_airports_1pax_paris_lisbon() -> TripSpec:
    return TripSpec(
        slug="09-same-city-two-airports-1pax-paris-lisbon",
        travelers=_INES,
        notes=(
            "Two one-way air tickets between Paris and Lisbon, using DIFFERENT "
            "Paris airports (CDG outbound, ORY inbound). Tests the engine's "
            "same-city-different-station collapse."
        ),
        primitives=(
            p.air_leg(
                from_city="Paris", to_city="Lisbon",
                from_identifier="CDG", to_identifier="LIS",
                departure_at=_dt(2027, 11, 1, 8, 0),
                arrival_at=_dt(2027, 11, 1, 10, 15),
                travelers=_INES, price_eur=155.00, pnr="CDGLISOUT",
            ),
            p.air_leg(
                from_city="Lisbon", to_city="Paris",
                from_identifier="LIS", to_identifier="ORY",
                departure_at=_dt(2027, 11, 4, 18, 0),
                arrival_at=_dt(2027, 11, 4, 20, 30),
                travelers=_INES, price_eur=149.00, pnr="LISORYBACK",
            ),
        ),
    )


# --------------------------------------------------------------------------- #
# 10 — divergent travelers mid-trip
# --------------------------------------------------------------------------- #


def _build_10_divergent_travelers_mid_trip_2pax() -> TripSpec:
    # Shared outbound to Frankfurt. Ines flies on to Berlin alone.
    return TripSpec(
        slug="10-divergent-travelers-mid-trip-2pax-paris-frankfurt-berlin",
        travelers=_INES_MARTA,
        notes=(
            "Both travelers fly Paris→Frankfurt together. Marta stays; "
            "Ines continues Frankfurt→Berlin alone."
        ),
        primitives=(
            p.air_leg(
                from_city="Paris", to_city="Frankfurt",
                from_identifier="CDG", to_identifier="FRA",
                departure_at=_dt(2027, 12, 1, 7, 0),
                arrival_at=_dt(2027, 12, 1, 8, 30),
                travelers=_INES_MARTA, price_eur=290.00, pnr="CDGFRADUAL",
            ),
            p.air_leg(
                from_city="Frankfurt", to_city="Berlin",
                from_identifier="FRA", to_identifier="BER",
                departure_at=_dt(2027, 12, 2, 9, 0),
                arrival_at=_dt(2027, 12, 2, 10, 0),
                travelers=_INES, price_eur=125.00, pnr="FRABERSOLO",
            ),
        ),
    )


# --------------------------------------------------------------------------- #
# 11 — supplementary with a venue routed onto a stop
# --------------------------------------------------------------------------- #


def _build_11_air_supplementary_venue_1pax_madrid_rome() -> TripSpec:
    return TripSpec(
        slug="11-air-supplementary-venue-1pax-madrid-rome",
        travelers=_INES,
        notes=(
            "Single air leg Madrid→Rome with a sightseeing supplementary doc "
            "venue in Rome. The venue routes onto the Rome stop."
        ),
        primitives=(
            p.air_leg(
                from_city="Madrid", to_city="Rome",
                from_identifier="MAD", to_identifier="FCO",
                departure_at=_dt(2028, 1, 5, 8, 30),
                arrival_at=_dt(2028, 1, 5, 11, 0),
                travelers=_INES, price_eur=178.00, pnr="MADROMAIR",
            ),
            p.supplementary_venue(
                city="Rome", kind="sightseeing",
                identifier="Retiro Botanical Pavilion",
                valid_from_at=_dt(2028, 1, 6, 9, 0),
                valid_to_at=_dt(2028, 1, 6, 18, 0),
                travelers=_INES, price_eur=28.00,
                reference_code="SIGHT-ROM-011",
            ),
        ),
    )


# --------------------------------------------------------------------------- #
# 12 — supplementary with no location → unattached document
# --------------------------------------------------------------------------- #


def _build_12_air_supplementary_no_location_1pax_madrid_rome() -> TripSpec:
    return TripSpec(
        slug="12-air-supplementary-no-location-1pax-madrid-rome",
        travelers=_INES,
        notes=(
            "Single air leg Madrid→Rome + a supplementary doc with NO venue / "
            "no station / no accommodation. Lands as an unattachedDocument."
        ),
        primitives=(
            p.air_leg(
                from_city="Madrid", to_city="Rome",
                from_identifier="MAD", to_identifier="FCO",
                departure_at=_dt(2028, 2, 1, 8, 30),
                arrival_at=_dt(2028, 2, 1, 11, 0),
                travelers=_INES, price_eur=185.00, pnr="MADROMAIR2",
            ),
            p.supplementary_no_location(
                travelers=_INES, price_eur=15.00,
                reference_code="VOUCHER-NO-LOC-012",
            ),
        ),
    )


# --------------------------------------------------------------------------- #
# 13 — sightseeing venue attached to an existing stop alongside a hotel
# --------------------------------------------------------------------------- #


def _build_13_air_hotel_venue_1pax_london_paris() -> TripSpec:
    return TripSpec(
        slug="13-air-hotel-venue-1pax-london-paris",
        travelers=_INES,
        notes=(
            "Air London→Paris + Paris hotel + Paris sightseeing venue. The "
            "venue attaches to the Paris hotel stop."
        ),
        primitives=(
            p.air_leg(
                from_city="London", to_city="Paris",
                from_identifier="LHR", to_identifier="CDG",
                departure_at=_dt(2028, 3, 1, 8, 0),
                arrival_at=_dt(2028, 3, 1, 10, 15),
                travelers=_INES, price_eur=160.00, pnr="LHRCDGAIR",
            ),
            p.hotel_stay(
                city="Paris", identifier="Hotel Lutece Marais",
                check_in_at=_dt(2028, 3, 1, 15, 0),
                check_out_at=_dt(2028, 3, 4, 11, 0),
                travelers=_INES, price_eur=420.00, confirmation_code="HTL-PAR-013",
            ),
            p.supplementary_venue(
                city="Paris", kind="sightseeing",
                identifier="Atelier Lumiere Museum",
                valid_from_at=_dt(2028, 3, 2, 10, 0),
                valid_to_at=_dt(2028, 3, 2, 18, 0),
                travelers=_INES, price_eur=32.00,
                reference_code="SIGHT-PAR-013",
            ),
        ),
    )


# --------------------------------------------------------------------------- #
# 14 — scan PDF mix: at least one rasterized PDF in the trip
# --------------------------------------------------------------------------- #


def _build_14_air_scan_mix_1pax_frankfurt_amsterdam() -> TripSpec:
    return TripSpec(
        slug="14-air-scan-mix-1pax-frankfurt-amsterdam",
        travelers=_INES,
        notes=(
            "Air out (rasterized scan) + Amsterdam hotel + air back (text) — "
            "covers the scan-PDF mix dimension."
        ),
        primitives=(
            p.air_leg(
                from_city="Frankfurt", to_city="Amsterdam",
                from_identifier="FRA", to_identifier="AMS",
                departure_at=_dt(2028, 4, 1, 9, 0),
                arrival_at=_dt(2028, 4, 1, 10, 30),
                travelers=_INES, price_eur=145.00, pnr="FRAAMSSCAN",
                rendering="rasterized",
            ),
            p.hotel_stay(
                city="Amsterdam", identifier="Hotel Mainufer Suites",
                check_in_at=_dt(2028, 4, 1, 15, 0),
                check_out_at=_dt(2028, 4, 3, 11, 0),
                travelers=_INES, price_eur=290.00, confirmation_code="HTL-AMS-014",
            ),
            p.air_leg(
                from_city="Amsterdam", to_city="Frankfurt",
                from_identifier="AMS", to_identifier="FRA",
                departure_at=_dt(2028, 4, 3, 17, 30),
                arrival_at=_dt(2028, 4, 3, 19, 0),
                travelers=_INES, price_eur=135.00, pnr="AMSFRARET",
            ),
        ),
    )


# --------------------------------------------------------------------------- #
# 15 — unreadable PDF in trip
# --------------------------------------------------------------------------- #


def _build_15_air_with_unreadable_pdf_1pax_madrid_rome() -> TripSpec:
    return TripSpec(
        slug="15-air-with-unreadable-pdf-1pax-madrid-rome",
        travelers=_INES,
        notes=(
            "Air return + an unreadable PDF in the manifest. The unreadable "
            "PDF is flagged expect_unreadable; trip's expected route built "
            "from the rest. Exercises functional-spec §2.6."
        ),
        primitives=(
            p.unreadable_pdf(placeholder_name="receipt-blank", travelers=_INES),
            p.air_return(
                from_city="Madrid", to_city="Rome",
                from_identifier="MAD", to_identifier="FCO",
                outbound_departure_at=_dt(2028, 5, 1, 8, 30),
                outbound_arrival_at=_dt(2028, 5, 1, 11, 0),
                return_departure_at=_dt(2028, 5, 5, 17, 30),
                return_arrival_at=_dt(2028, 5, 5, 20, 0),
                travelers=_INES, price_eur=315.00, pnr="MADROMRTU",
            ),
        ),
    )


# --------------------------------------------------------------------------- #
# 16 — real-shape: 3-city air trip with hotels in 2 cities
# --------------------------------------------------------------------------- #


def _build_16_air_3city_two_hotels_1pax_paris_berlin_prague() -> TripSpec:
    return TripSpec(
        slug="16-air-3city-two-hotels-1pax-paris-berlin-prague",
        travelers=_INES,
        notes=(
            "Real-shape: air Paris→Berlin, Berlin hotel, air Berlin→Prague, "
            "Prague hotel, air back to Paris."
        ),
        primitives=(
            p.air_leg(
                from_city="Paris", to_city="Berlin",
                from_identifier="CDG", to_identifier="BER",
                departure_at=_dt(2028, 6, 1, 8, 30),
                arrival_at=_dt(2028, 6, 1, 10, 30),
                travelers=_INES, price_eur=155.00, pnr="PARBERLEG1",
            ),
            p.hotel_stay(
                city="Berlin", identifier="Hotel Spreebogen Mitte",
                check_in_at=_dt(2028, 6, 1, 15, 0),
                check_out_at=_dt(2028, 6, 3, 11, 0),
                travelers=_INES, price_eur=280.00, confirmation_code="HTL-BER-016",
            ),
            p.air_leg(
                from_city="Berlin", to_city="Prague",
                from_identifier="BER", to_identifier="PRG",
                departure_at=_dt(2028, 6, 3, 13, 0),
                arrival_at=_dt(2028, 6, 3, 14, 15),
                travelers=_INES, price_eur=125.00, pnr="BERPRGLEG2",
            ),
            p.hotel_stay(
                city="Prague", identifier="Hotel Vltava Riverside",
                check_in_at=_dt(2028, 6, 3, 16, 0),
                check_out_at=_dt(2028, 6, 5, 11, 0),
                travelers=_INES, price_eur=240.00, confirmation_code="HTL-PRG-016",
            ),
            p.air_leg(
                from_city="Prague", to_city="Paris",
                from_identifier="PRG", to_identifier="CDG",
                departure_at=_dt(2028, 6, 5, 17, 0),
                arrival_at=_dt(2028, 6, 5, 19, 30),
                travelers=_INES, price_eur=170.00, pnr="PRGPARLEG3",
            ),
        ),
    )


# --------------------------------------------------------------------------- #
# 17 — real-shape: rail straight 3-city with hotel
# --------------------------------------------------------------------------- #


def _build_17_rail_3city_hotel_1pax_paris_frankfurt_amsterdam() -> TripSpec:
    return TripSpec(
        slug="17-rail-3city-hotel-1pax-paris-frankfurt-amsterdam",
        travelers=_INES,
        notes="Real-shape: rail Paris→Frankfurt, Frankfurt hotel, rail Frankfurt→Amsterdam.",
        primitives=(
            p.rail_leg(
                from_city="Paris", to_city="Frankfurt",
                from_identifier="Paris Gare du Nord", to_identifier="Frankfurt Hauptbahnhof",
                departure_at=_dt(2028, 7, 1, 8, 0),
                arrival_at=_dt(2028, 7, 1, 11, 30),
                travelers=_INES, price_eur=109.00, pnr="RAILPARFRA",
            ),
            p.hotel_stay(
                city="Frankfurt", identifier="Hotel Bockenheim Court",
                check_in_at=_dt(2028, 7, 1, 15, 0),
                check_out_at=_dt(2028, 7, 3, 11, 0),
                travelers=_INES, price_eur=270.00, confirmation_code="HTL-FRA-017",
            ),
            p.rail_leg(
                from_city="Frankfurt", to_city="Amsterdam",
                from_identifier="Frankfurt Hauptbahnhof", to_identifier="Amsterdam Centraal",
                departure_at=_dt(2028, 7, 3, 13, 0),
                arrival_at=_dt(2028, 7, 3, 17, 0),
                travelers=_INES, price_eur=99.00, pnr="RAILFRAAMS",
            ),
        ),
    )


# --------------------------------------------------------------------------- #
# 18 — real-shape: air + rail mix with airbnb
# --------------------------------------------------------------------------- #


def _build_18_air_rail_airbnb_1pax_madrid_rome_florence() -> TripSpec:
    return TripSpec(
        slug="18-air-rail-airbnb-1pax-madrid-rome-florence",
        travelers=_INES,
        notes="Air Madrid→Rome, Rome Airbnb, rail Rome→Florence.",
        primitives=(
            p.air_leg(
                from_city="Madrid", to_city="Rome",
                from_identifier="MAD", to_identifier="FCO",
                departure_at=_dt(2028, 8, 1, 8, 30),
                arrival_at=_dt(2028, 8, 1, 11, 0),
                travelers=_INES, price_eur=175.00, pnr="MADROMAIR3",
            ),
            p.airbnb_stay(
                city="Rome", identifier="Bright Flat in Malasana",
                check_in_at=_dt(2028, 8, 1, 15, 0),
                check_out_at=_dt(2028, 8, 4, 11, 0),
                travelers=_INES, price_eur=290.00, confirmation_code="ABN-ROM-018",
            ),
            p.rail_leg(
                from_city="Rome", to_city="Florence",
                from_identifier="Roma Termini", to_identifier="Firenze Santa Maria Novella",
                departure_at=_dt(2028, 8, 4, 13, 30),
                arrival_at=_dt(2028, 8, 4, 15, 15),
                travelers=_INES, price_eur=55.00, pnr="RAILROMFLO",
            ),
        ),
    )


# --------------------------------------------------------------------------- #
# 19 — real-shape: 2pax air + hotel + venue
# --------------------------------------------------------------------------- #


def _build_19_air_hotel_venue_2pax_london_paris() -> TripSpec:
    return TripSpec(
        slug="19-air-hotel-venue-2pax-london-paris",
        travelers=_INES_LUCAS,
        notes="2pax: air London→Paris + Paris hotel + Paris sightseeing.",
        primitives=(
            p.air_leg(
                from_city="London", to_city="Paris",
                from_identifier="LHR", to_identifier="CDG",
                departure_at=_dt(2028, 9, 1, 8, 0),
                arrival_at=_dt(2028, 9, 1, 10, 15),
                travelers=_INES_LUCAS, price_eur=315.00, pnr="LHRCDG2PAX",
            ),
            p.hotel_stay(
                city="Paris", identifier="Solana Paris Opera",
                check_in_at=_dt(2028, 9, 1, 15, 0),
                check_out_at=_dt(2028, 9, 4, 11, 0),
                travelers=_INES_LUCAS, price_eur=560.00, confirmation_code="HTL-PAR-019",
            ),
            p.supplementary_venue(
                city="Paris", kind="sightseeing",
                identifier="Tour Belvedere Observation Deck",
                valid_from_at=_dt(2028, 9, 2, 10, 0),
                valid_to_at=_dt(2028, 9, 2, 18, 0),
                travelers=_INES_LUCAS, price_eur=48.00,
                reference_code="SIGHT-PAR-019",
            ),
        ),
    )


# --------------------------------------------------------------------------- #
# 20 — real-shape: 3-city air + hotel with return
# --------------------------------------------------------------------------- #


def _build_20_air_3city_return_1pax_paris_berlin_vienna() -> TripSpec:
    return TripSpec(
        slug="20-air-3city-return-1pax-paris-berlin-vienna",
        travelers=_INES,
        notes="Air Paris→Berlin, Berlin hotel, air Berlin→Vienna, Vienna hotel, air Vienna→Paris.",
        primitives=(
            p.air_leg(
                from_city="Paris", to_city="Berlin",
                from_identifier="CDG", to_identifier="BER",
                departure_at=_dt(2028, 10, 1, 8, 0),
                arrival_at=_dt(2028, 10, 1, 9, 45),
                travelers=_INES, price_eur=150.00, pnr="PARBERL20A",
            ),
            p.hotel_stay(
                city="Berlin", identifier="Hotel Kreuzberg Park",
                check_in_at=_dt(2028, 10, 1, 15, 0),
                check_out_at=_dt(2028, 10, 3, 11, 0),
                travelers=_INES, price_eur=255.00, confirmation_code="HTL-BER-020",
            ),
            p.air_leg(
                from_city="Berlin", to_city="Vienna",
                from_identifier="BER", to_identifier="VIE",
                departure_at=_dt(2028, 10, 3, 13, 0),
                arrival_at=_dt(2028, 10, 3, 14, 30),
                travelers=_INES, price_eur=140.00, pnr="BERVIE20B",
            ),
            p.hotel_stay(
                city="Vienna", identifier="Hotel Westend Residenz",
                check_in_at=_dt(2028, 10, 3, 16, 0),
                check_out_at=_dt(2028, 10, 5, 11, 0),
                travelers=_INES, price_eur=290.00, confirmation_code="HTL-VIE-020",
            ),
            p.air_leg(
                from_city="Vienna", to_city="Paris",
                from_identifier="VIE", to_identifier="CDG",
                departure_at=_dt(2028, 10, 5, 13, 0),
                arrival_at=_dt(2028, 10, 5, 15, 30),
                travelers=_INES, price_eur=165.00, pnr="VIEPAR20C",
            ),
        ),
    )


def all_trips() -> list[TripSpec]:
    """Return every trip in the catalogue, in slug order."""
    return [
        _build_01_air_out_hotel_back_paris_lisbon_1pax(),
        _build_02_air_return_1pax_frankfurt_amsterdam(),
        _build_03_rail_out_hotel_back_1pax_paris_lisbon(),
        _build_04_bus_return_1pax_madrid_rome(),
        _build_05_air_rail_3city_1pax_paris_frankfurt_amsterdam(),
        _build_06_air_straight_2pax_madrid_rome(),
        _build_07_air_out_hotel_back_2pax_london_vienna(),
        _build_08_air_out_airbnb_back_1pax_berlin_prague(),
        _build_09_same_city_two_airports_1pax_paris_lisbon(),
        _build_10_divergent_travelers_mid_trip_2pax(),
        _build_11_air_supplementary_venue_1pax_madrid_rome(),
        _build_12_air_supplementary_no_location_1pax_madrid_rome(),
        _build_13_air_hotel_venue_1pax_london_paris(),
        _build_14_air_scan_mix_1pax_frankfurt_amsterdam(),
        _build_15_air_with_unreadable_pdf_1pax_madrid_rome(),
        _build_16_air_3city_two_hotels_1pax_paris_berlin_prague(),
        _build_17_rail_3city_hotel_1pax_paris_frankfurt_amsterdam(),
        _build_18_air_rail_airbnb_1pax_madrid_rome_florence(),
        _build_19_air_hotel_venue_2pax_london_paris(),
        _build_20_air_3city_return_1pax_paris_berlin_vienna(),
    ]
