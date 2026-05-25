"""Deterministic city pool for scenarios.

City codes are real IATA codes for readability, but the generator treats them as
opaque 3-letter labels. Picks are seeded so two scenarios with the same seed and
``count`` always pick the same cities in the same order.
"""

from __future__ import annotations

import random

CITY_POOL: tuple[str, ...] = (
    "WAW", "JFK", "LHR", "CDG", "MAD", "BCN", "BER", "AMS",
    "FRA", "ZRH", "VIE", "PRG", "DUB", "LIS", "ROM", "ATH",
    "IST", "SVO", "ARN", "CPH", "HEL", "OSL", "MXP", "MUC",
)


def pick_cities(rng: random.Random, count: int) -> list[str]:
    """Pick ``count`` distinct cities from the pool using ``rng``."""
    if count > len(CITY_POOL):
        raise ValueError(f"requested {count} cities, pool only has {len(CITY_POOL)}")
    return rng.sample(CITY_POOL, count)
