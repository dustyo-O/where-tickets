"""Compose one scenario from a ``ScenarioSpec``."""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Any

from .fragmenter import build_fragments_and_route
from .matrix import ScenarioSpec
from .orderings import apply_ordering
from .shapes import build_city_sequence


def _seed_for(spec: ScenarioSpec) -> int:
    payload = "|".join(
        [
            str(spec.index),
            spec.shape,
            str(spec.pax),
            str(spec.return_trip),
            str(spec.hotels),
            spec.ordering,
            str(spec.leg_count),
            spec.primary_mode,
        ]
    )
    digest = hashlib.sha256(payload.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


@dataclass(slots=True)
class GeneratedScenario:
    spec: ScenarioSpec
    fragments_in_emit_order: list[dict[str, Any]]
    expected_route: dict[str, Any]
    summary: str


def generate_scenario(spec: ScenarioSpec) -> GeneratedScenario:
    seed = _seed_for(spec)
    # Two independent RNGs so the cities/timings don't shift if we tweak the
    # ordering implementation later.
    shape_rng = random.Random(seed)
    order_rng = random.Random(seed ^ 0xA5A5A5A5)

    cities = build_city_sequence(spec.shape, shape_rng, spec.leg_count, spec.return_trip)
    chronological, expected_route = build_fragments_and_route(
        cities=cities,
        pax=spec.pax,
        primary_mode=spec.primary_mode,
        hotels=spec.hotels,
        scenario_slug=spec.slug,
        rng=shape_rng,
    )
    emit_order = apply_ordering(spec.ordering, chronological, order_rng)

    summary_parts = [
        f"{spec.shape} {spec.leg_count}-leg",
        f"{spec.pax} traveler{'s' if spec.pax != 1 else ''}",
        f"primary mode {spec.primary_mode}",
    ]
    if spec.return_trip:
        summary_parts.append("return trip")
    if spec.hotels:
        summary_parts.append("hotels")
    summary_parts.append(f"fragments {spec.ordering}")
    summary = ", ".join(summary_parts)

    return GeneratedScenario(
        spec=spec,
        fragments_in_emit_order=emit_order,
        expected_route=expected_route,
        summary=summary,
    )
