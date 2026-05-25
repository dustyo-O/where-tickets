"""Fragment-ordering strategies.

Each strategy takes a chronologically-ordered list of fragments and returns a
re-ordered list. The reordering is what the route-assembly engine will actually
receive — it must reconstruct chronology itself.
"""

from __future__ import annotations

import random
from typing import Any

Fragment = dict[str, Any]


def _forward(fragments: list[Fragment], rng: random.Random) -> list[Fragment]:
    return list(fragments)


def _reverse(fragments: list[Fragment], rng: random.Random) -> list[Fragment]:
    return list(reversed(fragments))


def _bisect(fragments: list[Fragment], rng: random.Random) -> list[Fragment]:
    midpoint = len(fragments) // 2
    return fragments[midpoint:] + fragments[:midpoint]


def _seeded_shuffle(fragments: list[Fragment], rng: random.Random) -> list[Fragment]:
    out = list(fragments)
    rng.shuffle(out)
    return out


ORDERINGS = {
    "forward": _forward,
    "reverse": _reverse,
    "bisect": _bisect,
    "seeded-shuffle": _seeded_shuffle,
}


def apply_ordering(
    name: str, fragments: list[Fragment], rng: random.Random
) -> list[Fragment]:
    if name not in ORDERINGS:
        raise ValueError(f"unknown ordering: {name}")
    return ORDERINGS[name](fragments, rng)
