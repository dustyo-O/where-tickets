"""Deterministic city pool for scenarios.

Cities are English printed exonyms (e.g. ``"Warsaw"``, ``"Paris"``) as they
would appear on a real travel document — what the engine identifies a stop by
after DUS-31 dropped the IATA-code pattern. Picks are seeded so two scenarios
with the same seed and ``count`` always pick the same cities in the same order.

``CITY_STATION_IDENTIFIER`` provides a stable identifier per ``(city, mode)``
pair so the generator's compact ``stations[]`` payload always carries the same
identifier for a given city / mode combination (DUS-31 Slice 3). Choice rules:

- ``air`` — the original IATA code from the pre-Slice-2 pool (Warsaw=WAW etc).
- ``rail`` — ``"<City> Hauptbahnhof"`` placeholder; arbitrary but stable.
- ``bus``  — ``"<City> Bus Terminal"`` placeholder; arbitrary but stable.

The exact strings are not load-bearing — the engine identifies stations by
``(city, kind, identifier)`` only when deduplicating within a stop's
``stations[]``. They just need to be deterministic across regenerations.
"""

from __future__ import annotations

import random

CITY_POOL: tuple[str, ...] = (
    "Warsaw", "New York", "London", "Paris", "Madrid", "Barcelona", "Berlin", "Amsterdam",
    "Frankfurt", "Zurich", "Vienna", "Prague", "Dublin", "Lisbon", "Rome", "Athens",
    "Istanbul", "Moscow", "Stockholm", "Copenhagen", "Helsinki", "Oslo", "Milan", "Munich",
)


# Per-city IATA airport codes from the pre-Slice-2 IATA pool. These are the
# codes the corpus emitted before the printed-name rename; they remain the
# stable identifier the generator stamps onto air stations.
_AIR_IATA: dict[str, str] = {
    "Warsaw": "WAW",
    "New York": "JFK",
    "London": "LHR",
    "Paris": "CDG",
    "Madrid": "MAD",
    "Barcelona": "BCN",
    "Berlin": "BER",
    "Amsterdam": "AMS",
    "Frankfurt": "FRA",
    "Zurich": "ZRH",
    "Vienna": "VIE",
    "Prague": "PRG",
    "Dublin": "DUB",
    "Lisbon": "LIS",
    "Rome": "ROM",
    "Athens": "ATH",
    "Istanbul": "IST",
    "Moscow": "SVO",
    "Stockholm": "ARN",
    "Copenhagen": "CPH",
    "Helsinki": "HEL",
    "Oslo": "OSL",
    "Milan": "MXP",
    "Munich": "MUC",
}


def station_identifier(city: str, mode: str) -> str:
    """Stable identifier the generator stamps onto a station for ``(city, mode)``."""
    if mode == "air":
        try:
            return _AIR_IATA[city]
        except KeyError as exc:
            msg = f"no IATA code mapped for air city {city!r}"
            raise ValueError(msg) from exc
    if mode == "rail":
        return f"{city} Hauptbahnhof"
    if mode == "bus":
        return f"{city} Bus Terminal"
    msg = f"unknown transit mode for station identifier: {mode!r}"
    raise ValueError(msg)


def station_kind(mode: str) -> str:
    """Map a transit mode to the matching ``Station.kind`` enum value."""
    return {"air": "airport", "rail": "rail_station", "bus": "bus_terminal"}[mode]


def accommodation_identifier(hotel_name: str) -> str:
    """Deterministic accommodation identifier for the compact ``accommodations[]``.

    DUS-31 Slice 4 replaces the optional ``hotelName`` field with a required
    printed ``identifier``. The fragmenter already picks a stable hotel name
    per stop slot (``HOTEL_NAMES[idx % len(HOTEL_NAMES)]``); this helper is a
    thin pass-through that documents the convention — the identifier IS the
    printed property name. Kept as a one-line helper so any future
    transformation (e.g. trimming, normalisation) lands in one place rather
    than scattered across the fragmenter.
    """
    return hotel_name


def pick_cities(rng: random.Random, count: int) -> list[str]:
    """Pick ``count`` distinct cities from the pool using ``rng``."""
    if count > len(CITY_POOL):
        raise ValueError(f"requested {count} cities, pool only has {len(CITY_POOL)}")
    return rng.sample(CITY_POOL, count)
