"""Deterministic data layer for the PDF corpus generator.

Given the same `(seed, count)` (or `(seed, day_offset, hour, minute)`), the
helpers in this module always return the same value. No randomness lives at
import time and no I/O happens at import time either. The randomized noise
layer (banners, T&C blocks, fonts, partial inclusion) lives in a separate
``noise.py`` module that is intentionally NOT part of this slice.

All datetimes are ISO 8601 local datetimes formatted as
``YYYY-MM-DDTHH:MM:SS`` (no timezone designator), matching the schema's
``isoLocalDatetime`` pattern.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

# Anchored epoch for all date math. Matches the fragment corpus convention.
EPOCH: str = "2027-03-01T00:00:00Z"
_EPOCH_DATETIME: datetime = datetime(2027, 3, 1, 0, 0, 0)


@dataclass(frozen=True, slots=True)
class City:
    """One city in the deterministic city pool.

    Carries everything a transit / lodging / venue scenario could possibly
    need to print on a document:

    - ``name``: city name as it appears on documents (e.g. "Paris").
    - ``country``: country name (currently unused by Slice 3 but useful for
      addresses on hotel/airbnb templates in Slice 4).
    - ``iata``: primary IATA code for the city's main airport. Used as the
      ``identifier`` on ``stations[]`` entries with ``kind == "airport"``.
    - ``rail_station``: printed rail-station name. Used as the
      ``identifier`` on ``stations[]`` entries with ``kind == "rail_station"``.
    - ``bus_terminal``: printed bus-terminal name. Used as the
      ``identifier`` on ``stations[]`` entries with ``kind == "bus_terminal"``.
    - ``hotel_pool``: 3-5 fictional hotel-property names that could plausibly
      appear in this city. Used as the ``identifier`` on ``accommodations[]``
      entries with ``kind == "hotel"``. Hand-picked, neutral names (no
      real-world brand collisions).
    - ``airbnb_pool``: 3-5 fictional Airbnb-style listing titles for this
      city. Used as the ``identifier`` on ``accommodations[]`` entries with
      ``kind == "airbnb"``. Hand-picked, neutral names.
    - ``sightseeing_pool``: 3-5 fictional sightseeing-venue names for this
      city (museums, towers, viewpoints, attractions). Used as the
      ``identifier`` on ``venues[]`` entries with ``kind == "sightseeing"``.
      Hand-picked, neutral names (no real-world brand collisions).
    - ``parking_pool``: 3-5 fictional parking-lot names for this city. Used
      as the ``identifier`` on ``venues[]`` entries with ``kind == "parking"``.
      Hand-picked, neutral names.
    - ``other_pool``: 3-5 fictional generic supplementary items for this
      city (city passes, welcome vouchers, etc). Used as the ``identifier``
      on ``venues[]`` entries with ``kind == "other"``. Hand-picked, neutral
      names.
    """

    name: str
    country: str
    iata: str
    rail_station: str
    bus_terminal: str
    hotel_pool: tuple[str, ...] = ()
    airbnb_pool: tuple[str, ...] = ()
    sightseeing_pool: tuple[str, ...] = ()
    parking_pool: tuple[str, ...] = ()
    other_pool: tuple[str, ...] = ()


# Hand-picked European spread. Real-world IATA codes + station/terminal names
# for printability; the generator treats them as opaque labels. Order is
# stable — any deterministic `random.Random(seed).sample(...)` over this tuple
# is reproducible.
CITY_POOL: tuple[City, ...] = (
    City(
        "Paris",
        "France",
        "CDG",
        "Paris Gare du Nord",
        "Paris Bercy Seine",
        hotel_pool=(
            "Hotel Saint-Cloud Rive Gauche",
            "Hotel Lutece Marais",
            "Solana Paris Opera",
            "Hotel Belmont Montparnasse",
        ),
        airbnb_pool=(
            "Cozy Atelier near Marais",
            "Sunny Loft above Canal Saint-Martin",
            "Quiet Studio in Le Marais",
            "Bright Apartment by Buttes-Chaumont",
        ),
        sightseeing_pool=(
            "Atelier Lumiere Museum",
            "Tour Belvedere Observation Deck",
            "Marais Heritage Walk",
            "Riverside Mosaic Gallery",
        ),
        parking_pool=(
            "Parking Saint-Lazare",
            "Parking Marais Centre",
            "Parquin Place Atlas",
            "Parking Rive Gauche Sud",
        ),
        other_pool=(
            "City Atlas 48h Pass",
            "Paseo Welcome Voucher",
            "Atlas Riverboat Pass",
            "Heritage Walks City Card",
        ),
    ),
    City("Lisbon", "Portugal", "LIS", "Lisboa Oriente", "Sete Rios"),
    City(
        "Madrid",
        "Spain",
        "MAD",
        "Madrid Atocha",
        "Madrid Mendez Alvaro",
        hotel_pool=(
            "Hotel Castellana Norte",
            "Hotel Atocha Garden",
            "Solana Madrid Salamanca",
            "Hotel Gran Via Mirador",
        ),
        airbnb_pool=(
            "Bright Flat in Malasana",
            "Penthouse near Retiro Park",
            "Charming Studio in La Latina",
            "Sun-soaked Apartment in Chueca",
        ),
        sightseeing_pool=(
            "Retiro Botanical Pavilion",
            "Mirador Atlas Tower",
            "Castellana Modern Art Hall",
            "La Latina Heritage Quarter",
        ),
        parking_pool=(
            "Parking Atocha Sur",
            "Garaje Atlas Castellana",
            "Parking Gran Via Mirador",
            "Parquin Plaza Mayor",
        ),
        other_pool=(
            "City Atlas Madrid 72h Pass",
            "Paseo Welcome Voucher",
            "Castellana Loop Pass",
            "Madrid Heritage Walks Card",
        ),
    ),
    City("Barcelona", "Spain", "BCN", "Barcelona Sants", "Barcelona Nord"),
    City(
        "Frankfurt",
        "Germany",
        "FRA",
        "Frankfurt Hauptbahnhof",
        "Frankfurt Hauptbahnhof Sud",
        hotel_pool=(
            "Hotel Mainufer Suites",
            "Hotel Bockenheim Court",
            "Solana Frankfurt Bankenviertel",
            "Hotel Westend Residenz",
        ),
        airbnb_pool=(
            "Riverfront Studio near Sachsenhausen",
            "Modern Loft in Nordend",
            "Quiet Apartment in Bornheim",
            "Bright Suite near Roemerberg",
        ),
        sightseeing_pool=(
            "Mainufer Riverside Museum",
            "Roemerberg Heritage Square Tour",
            "Westend Skyline Observatory",
            "Nordend Modern Gallery",
        ),
        parking_pool=(
            "Parking Hauptbahnhof Sud",
            "Parquin Bankenviertel",
            "Garaje Atlas Mainufer",
            "Parking Westend Court",
        ),
        other_pool=(
            "City Atlas Frankfurt Pass",
            "Mainufer Welcome Voucher",
            "Skyline Atlas Pass",
            "Nordend Heritage Card",
        ),
    ),
    City(
        "Berlin",
        "Germany",
        "BER",
        "Berlin Hauptbahnhof",
        "Berlin ZOB",
        hotel_pool=(
            "Hotel Spreebogen Mitte",
            "Hotel Kreuzberg Park",
            "Solana Berlin Tiergarten",
            "Hotel Charlottenburg Residenz",
        ),
        airbnb_pool=(
            "Industrial Loft in Prenzlauer Berg",
            "Sunny Flat near Boxhagener Platz",
            "Cozy Studio in Kreuzberg",
            "Bright Apartment near Tempelhofer Feld",
        ),
        sightseeing_pool=(
            "Spreebogen River Museum",
            "Tiergarten Observation Tower",
            "Kreuzberg Modern Art Hall",
            "Charlottenburg Heritage Walk",
        ),
        parking_pool=(
            "Parking Hauptbahnhof Mitte",
            "Parquin Tiergarten Sud",
            "Garaje Atlas Kreuzberg",
            "Parking Charlottenburg Court",
        ),
        other_pool=(
            "City Atlas Berlin 48h Pass",
            "Spree Welcome Voucher",
            "Tiergarten Loop Pass",
            "Berlin Heritage Walks Card",
        ),
    ),
    City("Munich", "Germany", "MUC", "Munchen Hauptbahnhof", "Munchen ZOB"),
    City(
        "Amsterdam", "Netherlands", "AMS", "Amsterdam Centraal", "Amsterdam Sloterdijk"
    ),
    City("Brussels", "Belgium", "BRU", "Bruxelles Midi", "Bruxelles Nord Coach"),
    City("Vienna", "Austria", "VIE", "Wien Hauptbahnhof", "Wien Erdberg VIB"),
    City("Zurich", "Switzerland", "ZRH", "Zurich Hauptbahnhof", "Zurich Sihlquai"),
    City("Geneva", "Switzerland", "GVA", "Geneve Cornavin", "Geneve Gare Routiere"),
    City("Rome", "Italy", "FCO", "Roma Termini", "Roma Tiburtina Bus"),
    City("Milan", "Italy", "MXP", "Milano Centrale", "Milano Lampugnano"),
    City(
        "Florence",
        "Italy",
        "FLR",
        "Firenze Santa Maria Novella",
        "Firenze Villa Costanza",
    ),
    City(
        "Venice",
        "Italy",
        "VCE",
        "Venezia Santa Lucia",
        "Venezia Tronchetto Bus",
    ),
    City("Naples", "Italy", "NAP", "Napoli Centrale", "Napoli Metropark"),
    City("Athens", "Greece", "ATH", "Athens Central Station", "Athens KTEL Kifisos"),
    City(
        "Prague",
        "Czechia",
        "PRG",
        "Praha hlavni nadrazi",
        "Praha UAN Florenc",
        hotel_pool=(
            "Hotel Vltava Riverside",
            "Hotel Vinohrady Court",
            "Solana Prague Old Town",
            "Hotel Mala Strana Residenz",
        ),
        airbnb_pool=(
            "Charming Flat in Zizkov",
            "Quiet Studio near Letna Park",
            "Bright Apartment in Karlin",
            "Cozy Loft near Old Town Square",
        ),
        sightseeing_pool=(
            "Vltava Riverside Museum",
            "Letna Hill Observation Deck",
            "Old Town Heritage Walk",
            "Mala Strana Modern Gallery",
        ),
        parking_pool=(
            "Parking Hlavni Nadrazi",
            "Parquin Vinohrady Court",
            "Garaje Atlas Karlin",
            "Parking Mala Strana Sud",
        ),
        other_pool=(
            "City Atlas Prague 72h Pass",
            "Vltava Welcome Voucher",
            "Old Town Loop Pass",
            "Prague Heritage Walks Card",
        ),
    ),
    City(
        "Warsaw",
        "Poland",
        "WAW",
        "Warszawa Centralna",
        "Warszawa Zachodnia Bus",
    ),
    City("Budapest", "Hungary", "BUD", "Budapest Keleti", "Budapest Nepliget"),
    City("Copenhagen", "Denmark", "CPH", "Kobenhavn H", "Ingerslevsgade Coach"),
    City("Stockholm", "Sweden", "ARN", "Stockholm Central", "Stockholm Cityterminalen"),
    City("Oslo", "Norway", "OSL", "Oslo Sentralstasjon", "Oslo Bussterminal"),
    City("Helsinki", "Finland", "HEL", "Helsinki Central", "Helsinki Kamppi Coach"),
    City("Dublin", "Ireland", "DUB", "Dublin Connolly", "Dublin Busaras"),
    City(
        "London",
        "United Kingdom",
        "LHR",
        "London St Pancras",
        "London Victoria Coach",
        hotel_pool=(
            "Hotel Kensington Garden Court",
            "Hotel Bloomsbury Square",
            "Solana London Southbank",
            "Hotel Marylebone Residenz",
        ),
        airbnb_pool=(
            "Bright Flat in Shoreditch",
            "Garden Studio near Hampstead Heath",
            "Quiet Apartment in Notting Hill",
            "Cozy Loft near Brick Lane",
        ),
        sightseeing_pool=(
            "Southbank Riverside Museum",
            "Marylebone Heritage Tower",
            "Kensington Gallery of Light",
            "Bloomsbury Walking Tour",
        ),
        parking_pool=(
            "Parking St Pancras North",
            "Parquin Southbank Atlas",
            "Garaje Atlas Kensington",
            "Parking Marylebone Court",
        ),
        other_pool=(
            "City Atlas London 48h Pass",
            "Southbank Welcome Voucher",
            "Heritage Loop Pass",
            "London Walks City Card",
        ),
    ),
    City(
        "Edinburgh",
        "United Kingdom",
        "EDI",
        "Edinburgh Waverley",
        "Edinburgh Bus Station",
    ),
    City("Porto", "Portugal", "OPO", "Porto Sao Bento", "Porto Campanha Bus"),
    City("Reykjavik", "Iceland", "KEF", "Reykjavik BSI", "Reykjavik Mjodd"),
)


# Mix of single- and multi-word names. No real people — sampled fictional
# personas. Stable order so sampling is reproducible.
TRAVELER_POOL: tuple[str, ...] = (
    "Alice Example",
    "Bob Sample",
    "Carla Mendes",
    "Diego Romano",
    "Eva Lindqvist",
    "Felix Bauer",
    "Gabriela Rossi",
    "Hugo van der Berg",
    "Ines Marques",
    "Julien Lefevre",
    "Karin Andersson",
    "Lucas Costa",
    "Marta Kowalski",
    "Niko Virtanen",
    "Olga Ivanova",
    "Pierre Dubois",
    "Quentin Moreau",
    "Rita Santos",
    "Sven Olsen",
    "Tara Murphy",
)


# Per-document_type currency + price-range pools. Slice 3 only exercises
# `air_ticket`; the other entries are pre-seeded for forward compat.
# Forward-compat: tune these in slice 4 once rail/bus/lodging templates land.
type CurrencyCode = str
type PriceRange = tuple[float, float]

PRICES_BY_DOCUMENT_TYPE: dict[str, dict[CurrencyCode, PriceRange]] = {
    "air_ticket": {
        "EUR": (89.50, 489.00),
        "GBP": (74.00, 412.00),
    },
    # TODO(slice-4): tune rail / bus ranges once those templates exist.
    "rail_ticket": {"EUR": (19.00, 189.00)},
    "bus_ticket": {"EUR": (9.00, 79.00)},
    "hotel_booking": {"EUR": (79.00, 359.00)},
    "airbnb_booking": {"EUR": (59.00, 289.00)},
    "supplementary": {"EUR": (5.00, 49.00)},
}


def _seeded(seed: int, *, salt: str) -> random.Random:
    """Build a `random.Random` instance from an integer seed + a string salt.

    The salt isolates one kind of pick (cities) from another (travelers) so
    they don't share entropy and accidentally correlate.
    """
    return random.Random(f"{seed}:{salt}")


def pick_cities(seed: int, count: int) -> tuple[City, ...]:
    """Pick ``count`` distinct cities deterministically from `CITY_POOL`.

    Same `(seed, count)` -> same tuple, every time, across processes.
    """
    if count < 1:
        raise ValueError(f"count must be >= 1, got {count}")
    if count > len(CITY_POOL):
        raise ValueError(f"requested {count} cities, pool only has {len(CITY_POOL)}")
    rng = _seeded(seed, salt="cities")
    return tuple(rng.sample(CITY_POOL, count))


def pick_travelers(seed: int, count: int) -> tuple[str, ...]:
    """Pick ``count`` distinct traveler names deterministically.

    Same `(seed, count)` -> same tuple, every time.
    """
    if count < 1:
        raise ValueError(f"count must be >= 1, got {count}")
    if count > len(TRAVELER_POOL):
        raise ValueError(
            f"requested {count} travelers, pool only has {len(TRAVELER_POOL)}"
        )
    rng = _seeded(seed, salt="travelers")
    return tuple(rng.sample(TRAVELER_POOL, count))


def pick_datetime(
    seed: int,
    day_offset: int,
    hour: int = 8,
    minute: int = 30,
) -> str:
    """Return a deterministic ISO 8601 local datetime string.

    The base time is ``EPOCH + day_offset days`` at the given ``hour`` /
    ``minute``. ``seed`` is included in the function signature so a future
    minor-jitter implementation (e.g. shift by N minutes seeded from a scenario
    axis) stays drop-in compatible; today the jitter is zero so callers get a
    stable, predictable output.
    """
    if not 0 <= hour <= 23:
        raise ValueError(f"hour must be in 0..23, got {hour}")
    if not 0 <= minute <= 59:
        raise ValueError(f"minute must be in 0..59, got {minute}")
    # `seed` is consulted to make the contract explicit even though the
    # current implementation is purely deterministic on `(day_offset, hour,
    # minute)`. Touching the RNG keeps callers honest that they MUST pass a
    # stable seed if they ever want stable output.
    _seeded(seed, salt="datetime")
    moment = _EPOCH_DATETIME + timedelta(days=day_offset, hours=hour, minutes=minute)
    return moment.strftime("%Y-%m-%dT%H:%M:%S")


def pick_transit_arrival(
    seed: int,
    day_offset: int,
    departure_hour: int,
    departure_minute: int,
    *,
    duration_hours: int,
    duration_minutes: int = 0,
) -> str:
    """Return an arrival ISO local datetime ``duration`` after the departure.

    Helper for transit modes (rail, bus) whose journey durations differ from
    the air-ticket convention. Encoded as integer hours+minutes so both
    inputs (seed, day_offset, departure_hour, departure_minute) and the
    duration are deterministic and easy to reason about.

    Same ``(seed, day_offset, departure_hour, departure_minute, duration_*)``
    -> same ISO 8601 datetime string. The departure-side call should pass
    the SAME ``(seed, day_offset, hour, minute)`` to :func:`pick_datetime`
    so the leg is internally consistent.
    """
    if not 0 <= departure_hour <= 23:
        raise ValueError(f"departure_hour must be in 0..23, got {departure_hour}")
    if not 0 <= departure_minute <= 59:
        raise ValueError(f"departure_minute must be in 0..59, got {departure_minute}")
    if duration_hours < 0:
        raise ValueError(f"duration_hours must be >= 0, got {duration_hours}")
    if not 0 <= duration_minutes <= 59:
        raise ValueError(f"duration_minutes must be in 0..59, got {duration_minutes}")
    # Touching the RNG keeps the seeded-determinism contract symmetrical with
    # `pick_datetime`; the actual math is purely arithmetic.
    _seeded(seed, salt="transit-arrival")
    moment = _EPOCH_DATETIME + timedelta(
        days=day_offset,
        hours=departure_hour + duration_hours,
        minutes=departure_minute + duration_minutes,
    )
    return moment.strftime("%Y-%m-%dT%H:%M:%S")


def pick_accommodation_dates(
    seed: int,
    day_offset: int,
    nights: int,
) -> tuple[str, str]:
    """Return ``(check_in_datetime, check_out_datetime)`` ISO local strings.

    Check-in is at 15:00 local, check-out at 11:00 local — the industry-
    standard hotel cadence. The stay spans ``nights`` calendar nights, so
    ``check_out`` lands ``nights`` days after ``check_in``.

    Same ``(seed, day_offset, nights)`` -> same pair, every time. ``seed`` is
    consulted to keep determinism contracts symmetric with
    :func:`pick_datetime` even though the math is purely arithmetic today.
    """
    if nights < 1:
        raise ValueError(f"nights must be >= 1, got {nights}")
    _seeded(seed, salt="accommodation")
    check_in = _EPOCH_DATETIME + timedelta(days=day_offset, hours=15, minutes=0)
    check_out = _EPOCH_DATETIME + timedelta(
        days=day_offset + nights, hours=11, minutes=0
    )
    return (
        check_in.strftime("%Y-%m-%dT%H:%M:%S"),
        check_out.strftime("%Y-%m-%dT%H:%M:%S"),
    )


def pick_validity_window(
    seed: int,
    day_offset: int,
    kind: str,
) -> tuple[str, str]:
    """Return ``(valid_from_datetime, valid_to_datetime)`` ISO local strings.

    Shape per ``kind`` so the supplementary template prints a plausible
    validity window for each variant:

    - ``sightseeing``: same-day, 09:00 -> 18:00 (timed-entry tourist slot).
    - ``parking``: 1-3 calendar days, 14:00 (entry) -> 11:00 (exit) on the
      exit day. The number of days is deterministically picked from
      ``seed``.
    - ``other``: 2-4 calendar days, 00:00 -> 23:59 on the last day (a
      city-pass-style window). The number of days is deterministically
      picked from ``seed``.

    Same ``(seed, day_offset, kind)`` -> same pair, every time. ``seed`` is
    consulted for the duration-dependent variants and salts the RNG so
    re-rolls stay stable.
    """
    if kind not in ("sightseeing", "parking", "other"):
        raise ValueError(
            f"kind must be one of 'sightseeing'/'parking'/'other', got {kind!r}"
        )
    rng = _seeded(seed, salt=f"venue-validity:{kind}")
    match kind:
        case "sightseeing":
            start = _EPOCH_DATETIME + timedelta(days=day_offset, hours=9, minutes=0)
            end = _EPOCH_DATETIME + timedelta(days=day_offset, hours=18, minutes=0)
        case "parking":
            # 1..3 calendar days inclusive; entry at 14:00 on day_offset,
            # exit at 11:00 on day_offset + days.
            days = rng.randint(1, 3)
            start = _EPOCH_DATETIME + timedelta(days=day_offset, hours=14, minutes=0)
            end = _EPOCH_DATETIME + timedelta(
                days=day_offset + days, hours=11, minutes=0
            )
        case _:  # "other"
            # 2..4 calendar days inclusive; 00:00 on day_offset, 23:59 on
            # day_offset + (days - 1) so the window spans `days` calendar
            # days from start to end inclusive.
            days = rng.randint(2, 4)
            start = _EPOCH_DATETIME + timedelta(days=day_offset, hours=0, minutes=0)
            end = _EPOCH_DATETIME + timedelta(
                days=day_offset + days - 1, hours=23, minutes=59
            )
    return (
        start.strftime("%Y-%m-%dT%H:%M:%S"),
        end.strftime("%Y-%m-%dT%H:%M:%S"),
    )


# Fictional brand metadata per supplementary venue kind. The supplementary
# template branches on `venues[0].kind` and pulls header copy + reference
# prefix from this table so the three variants visually differ. None of these
# names match any real-world brand (verified by hand on 2026-05-31).
SUPPLEMENTARY_BRANDS: dict[str, dict[str, str]] = {
    "sightseeing": {
        "name": "Paseo Tickets",
        "tagline": "Fictional Tickets, Real Test Data",
        "ref_prefix": "PT",
        "doc_label": "Admission Ticket",
        "kind_word": "Sightseeing admission",
    },
    "parking": {
        "name": "Parquin",
        "tagline": "Fictional Parking, Real Test Data",
        "ref_prefix": "PQ",
        "doc_label": "Parking Reservation",
        "kind_word": "Parking reservation",
    },
    "other": {
        "name": "City Atlas",
        "tagline": "Fictional Passes, Real Test Data",
        "ref_prefix": "CA",
        "doc_label": "City Pass",
        "kind_word": "City pass",
    },
}


def pick_price(
    seed: int,
    document_type: str,
    *,
    currency: str | None = None,
) -> tuple[float, str]:
    """Pick a `(amount, currency)` pair deterministically for a document type.

    Amount is rounded to 2 decimals to match how prices print on tickets.
    If ``currency`` is ``None``, one is picked deterministically from the
    document type's currency pool.
    """
    pool = PRICES_BY_DOCUMENT_TYPE.get(document_type)
    if not pool:
        raise ValueError(f"no price pool for document_type={document_type!r}")
    rng = _seeded(seed, salt="price")
    if currency is None:
        currency = rng.choice(sorted(pool.keys()))
    low, high = pool[currency]
    amount = round(rng.uniform(low, high), 2)
    return amount, currency


__all__ = [
    "EPOCH",
    "City",
    "CITY_POOL",
    "TRAVELER_POOL",
    "PRICES_BY_DOCUMENT_TYPE",
    "SUPPLEMENTARY_BRANDS",
    "pick_cities",
    "pick_travelers",
    "pick_datetime",
    "pick_transit_arrival",
    "pick_accommodation_dates",
    "pick_price",
    "pick_validity_window",
]
