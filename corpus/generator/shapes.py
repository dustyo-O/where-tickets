"""Route-shape generators.

A shape produces an ordered list of city codes representing the city sequence a
traveler visits. ``cities[i] -> cities[i+1]`` is one transit (one "hop"). Stops
are the unique-by-position cities between hops.

Supported shapes:
- ``straight``: a -> b -> c -> ...
- ``circle``: a -> b -> c -> d -> b -> e (revisits a hub mid-trip then continues)
- ``star``: hub-and-spoke: a -> b -> c -> b -> d -> b -> e (b is the hub)

The ``return_trip`` flag appends a final hop back to the origin if it isn't
already the last city.
"""

from __future__ import annotations

import random

from .cities import pick_cities


def _straight(rng: random.Random, leg_count: int) -> list[str]:
    cities = pick_cities(rng, leg_count + 1)
    return cities


def _circle(rng: random.Random, leg_count: int) -> list[str]:
    # leg_count is the number of hops; insert one revisit to the second city.
    # Example for 4 hops: a -> b -> c -> d -> b  (5 cities, 4 hops, last == 2nd)
    if leg_count < 3:
        leg_count = 3
    unique = pick_cities(rng, leg_count)
    sequence = list(unique)
    sequence.append(unique[1])  # revisit hub mid-trip
    return sequence


def _star(rng: random.Random, leg_count: int) -> list[str]:
    # Hub-and-spoke: pick hub + spokes; visit each spoke from the hub.
    # Pattern for 4 hops: a(origin) -> b(hub) -> c -> b -> d  (4 hops, 5 cities slots)
    if leg_count < 3:
        leg_count = 3
    # Number of spoke visits after the origin->hub hop:
    # each spoke costs 2 hops (hub->spoke + spoke->hub), minus the final hop's return.
    # We pick: origin, hub, then spokes. For ``leg_count`` hops total:
    #   1 hop origin->hub, then alternating hub->spoke / spoke->hub.
    spokes_needed = max(1, (leg_count - 1 + 1) // 2)
    chosen = pick_cities(rng, 2 + spokes_needed)
    origin, hub, *spokes = chosen
    sequence: list[str] = [origin, hub]
    spoke_idx = 0
    while len(sequence) - 1 < leg_count:
        sequence.append(spokes[spoke_idx % len(spokes)])
        spoke_idx += 1
        if len(sequence) - 1 >= leg_count:
            break
        sequence.append(hub)
    return sequence


_SHAPE_FUNCS = {
    "straight": _straight,
    "circle": _circle,
    "star": _star,
}


def build_city_sequence(
    shape: str, rng: random.Random, leg_count: int, return_trip: bool
) -> list[str]:
    """Build the ordered city sequence for a scenario."""
    if shape not in _SHAPE_FUNCS:
        raise ValueError(f"unknown shape: {shape}")
    sequence = _SHAPE_FUNCS[shape](rng, leg_count)
    if return_trip and sequence[-1] != sequence[0]:
        sequence.append(sequence[0])
    return sequence
