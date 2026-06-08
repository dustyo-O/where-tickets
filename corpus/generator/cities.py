"""Deterministic city pool for scenarios.

Cities are English printed exonyms (e.g. ``"Warsaw"``, ``"Paris"``) as they
would appear on a real travel document — what the engine identifies a stop by
after DUS-31 dropped the IATA-code pattern. Picks are seeded so two scenarios
with the same seed and ``count`` always pick the same cities in the same order.
"""

from __future__ import annotations

import random

CITY_POOL: tuple[str, ...] = (
    "Warsaw", "New York", "London", "Paris", "Madrid", "Barcelona", "Berlin", "Amsterdam",
    "Frankfurt", "Zurich", "Vienna", "Prague", "Dublin", "Lisbon", "Rome", "Athens",
    "Istanbul", "Moscow", "Stockholm", "Copenhagen", "Helsinki", "Oslo", "Milan", "Munich",
)


def pick_cities(rng: random.Random, count: int) -> list[str]:
    """Pick ``count`` distinct cities from the pool using ``rng``."""
    if count > len(CITY_POOL):
        raise ValueError(f"requested {count} cities, pool only has {len(CITY_POOL)}")
    return rng.sample(CITY_POOL, count)
