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
    - ``rail_station``: printed rail/bus station name. Used as the
      ``identifier`` on ``stations[]`` entries with ``kind == "rail_station"``
      or ``kind == "bus_terminal"`` once Slice 4 wires rail and bus tickets.
    """

    name: str
    country: str
    iata: str
    rail_station: str


# Hand-picked European spread. Real-world IATA codes for printability; the
# generator treats them as opaque labels. Order is stable — any deterministic
# `random.Random(seed).sample(...)` over this tuple is reproducible.
CITY_POOL: tuple[City, ...] = (
    City("Paris", "France", "CDG", "Paris Gare du Nord"),
    City("Lisbon", "Portugal", "LIS", "Lisboa Oriente"),
    City("Madrid", "Spain", "MAD", "Madrid Atocha"),
    City("Barcelona", "Spain", "BCN", "Barcelona Sants"),
    City("Frankfurt", "Germany", "FRA", "Frankfurt Hauptbahnhof"),
    City("Berlin", "Germany", "BER", "Berlin Hauptbahnhof"),
    City("Munich", "Germany", "MUC", "Munchen Hauptbahnhof"),
    City("Amsterdam", "Netherlands", "AMS", "Amsterdam Centraal"),
    City("Brussels", "Belgium", "BRU", "Bruxelles Midi"),
    City("Vienna", "Austria", "VIE", "Wien Hauptbahnhof"),
    City("Zurich", "Switzerland", "ZRH", "Zurich Hauptbahnhof"),
    City("Geneva", "Switzerland", "GVA", "Geneve Cornavin"),
    City("Rome", "Italy", "FCO", "Roma Termini"),
    City("Milan", "Italy", "MXP", "Milano Centrale"),
    City("Florence", "Italy", "FLR", "Firenze Santa Maria Novella"),
    City("Venice", "Italy", "VCE", "Venezia Santa Lucia"),
    City("Naples", "Italy", "NAP", "Napoli Centrale"),
    City("Athens", "Greece", "ATH", "Athens Central Station"),
    City("Prague", "Czechia", "PRG", "Praha hlavni nadrazi"),
    City("Warsaw", "Poland", "WAW", "Warszawa Centralna"),
    City("Budapest", "Hungary", "BUD", "Budapest Keleti"),
    City("Copenhagen", "Denmark", "CPH", "Kobenhavn H"),
    City("Stockholm", "Sweden", "ARN", "Stockholm Central"),
    City("Oslo", "Norway", "OSL", "Oslo Sentralstasjon"),
    City("Helsinki", "Finland", "HEL", "Helsinki Central"),
    City("Dublin", "Ireland", "DUB", "Dublin Connolly"),
    City("London", "United Kingdom", "LHR", "London St Pancras"),
    City("Edinburgh", "United Kingdom", "EDI", "Edinburgh Waverley"),
    City("Porto", "Portugal", "OPO", "Porto Sao Bento"),
    City("Reykjavik", "Iceland", "KEF", "Reykjavik BSI"),
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
    "pick_cities",
    "pick_travelers",
    "pick_datetime",
    "pick_price",
]
