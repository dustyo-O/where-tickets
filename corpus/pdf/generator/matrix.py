"""Scenario matrix for the PDF corpus generator.

Slice 3 ships ~25 `air_ticket` scenarios across:

- ``shape`` axis: ``one_leg`` (origin -> destination, 2 stations) and
  ``return`` (origin -> destination + destination -> origin in one PDF; 3
  station entries where the destination is the layover carrying both an
  arrival and a departure datetime, and the origin appears twice as the
  outbound departure and the return arrival).
- ``travelers`` axis: 1 or 2 passenger names on the ticket.
- ``city_pair`` axis: 6 hand-curated origin/destination pairs sampled from
  the data layer's ``CITY_POOL`` so coverage isn't all Paris-Lisbon.

Total: ``6 city_pairs * 2 shapes * 2 traveler_counts = 24`` scenarios — inside
the spec's [20, 30] target window for "~25".

Other ``document_type`` values (rail, bus, hotel, airbnb, supplementary) are
deferred to Slice 4. The rendering / noise / templates / `__main__` glue is
deferred to later sub-tasks.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal

from corpus.pdf.generator.data import (
    CITY_POOL,
    City,
    pick_datetime,
    pick_price,
    pick_travelers,
)

type Shape = Literal["one_leg", "return"]
type DocumentType = Literal["air_ticket"]

# Each entry is an index pair into the data layer's `CITY_POOL`. Picking
# concrete indices (rather than re-rolling per-scenario) keeps coverage
# explicit and predictable in code review.
#
# Indices follow `corpus/pdf/generator/data.CITY_POOL` order:
#   0 Paris, 1 Lisbon, 2 Madrid, 3 Barcelona, 4 Frankfurt, 5 Berlin,
#   6 Munich, 7 Amsterdam, 8 Brussels, 9 Vienna, 10 Zurich, 11 Geneva,
#   12 Rome, 13 Milan, 14 Florence, 15 Venice, 16 Naples, 17 Athens,
#   18 Prague, 19 Warsaw, 20 Budapest, 21 Copenhagen, 22 Stockholm,
#   23 Oslo, 24 Helsinki, 25 Dublin, 26 London, 27 Edinburgh,
#   28 Porto, 29 Reykjavik
CITY_PAIR_INDICES: tuple[tuple[int, int], ...] = (
    (0, 1),  # Paris      -> Lisbon
    (4, 7),  # Frankfurt  -> Amsterdam
    (2, 12),  # Madrid     -> Rome
    (26, 9),  # London     -> Vienna
    (5, 21),  # Berlin     -> Copenhagen
    (18, 28),  # Prague     -> Porto
)

SHAPES: tuple[Shape, ...] = ("one_leg", "return")
TRAVELER_COUNTS: tuple[int, ...] = (1, 2)


def _slugify(name: str) -> str:
    """Turn a city/printable string into a URL-safe slug fragment."""
    lower = name.lower()
    # Strip diacritics-ish chars by keeping ASCII letters + digits + hyphens.
    cleaned = re.sub(r"[^a-z0-9]+", "-", lower).strip("-")
    return cleaned or "city"


def _noise_seed_for(scenario_id: str) -> int:
    """Stable SHA-256 derived integer seed for the (future) noise layer.

    Stored on the spec so a later sub-task's noise functions can reproduce
    exactly the layout that was generated.
    """
    digest = hashlib.sha256(scenario_id.encode("utf-8")).digest()
    # 8 bytes -> uint64; plenty of entropy for `random.Random`.
    return int.from_bytes(digest[:8], "big")


def _scenario_seed_for(scenario_id: str) -> int:
    """Stable integer seed for the deterministic data layer.

    Distinct from the noise seed: data must stay invariant across
    regenerations while noise is allowed to wander.
    """
    digest = hashlib.sha256(("data:" + scenario_id).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


@dataclass(frozen=True, slots=True)
class ScenarioSpec:
    """One generated air-ticket scenario.

    `scenario_id` is the canonical stable identity. `expected_fields()` is the
    payload that becomes `expected-fields.json` once rendering lands.
    """

    scenario_id: str
    document_type: DocumentType
    shape: Shape
    travelers: int
    origin_index: int
    destination_index: int
    noise_seed: int

    def _resolve_cities(self) -> tuple[City, City]:
        """Resolve the origin / destination ``City`` records.

        Pulled out of ``expected_fields()`` so any downstream consumer (e.g.
        the future render pipeline) can reuse the same lookup.
        """
        return CITY_POOL[self.origin_index], CITY_POOL[self.destination_index]

    def expected_fields(self) -> dict[str, Any]:
        """Build the schema-compliant payload for this scenario.

        Pure function of the dataclass fields + the data layer. JSON-stable:
        keys are explicit, lists are deterministically ordered. Validates
        against `corpus/pdf/schema/expected-fields.schema.json` and passes
        the additional rules in `corpus/pdf/validate.py`.
        """
        origin, destination = self._resolve_cities()
        seed = _scenario_seed_for(self.scenario_id)

        outbound_departure = pick_datetime(seed, day_offset=10, hour=8, minute=30)
        outbound_arrival = pick_datetime(seed, day_offset=10, hour=10, minute=45)

        stations: list[dict[str, Any]]
        if self.shape == "one_leg":
            stations = [
                {
                    "city": origin.name,
                    "kind": "airport",
                    "identifier": origin.iata,
                    "departure_datetime": outbound_departure,
                },
                {
                    "city": destination.name,
                    "kind": "airport",
                    "identifier": destination.iata,
                    "arrival_datetime": outbound_arrival,
                },
            ]
        else:  # "return"
            return_departure = pick_datetime(seed, day_offset=14, hour=18, minute=15)
            return_arrival = pick_datetime(seed, day_offset=14, hour=20, minute=30)
            stations = [
                {
                    "city": origin.name,
                    "kind": "airport",
                    "identifier": origin.iata,
                    "departure_datetime": outbound_departure,
                },
                {
                    "city": destination.name,
                    "kind": "airport",
                    "identifier": destination.iata,
                    "arrival_datetime": outbound_arrival,
                    "departure_datetime": return_departure,
                },
                {
                    "city": origin.name,
                    "kind": "airport",
                    "identifier": origin.iata,
                    "arrival_datetime": return_arrival,
                },
            ]

        traveler_names = list(pick_travelers(seed, self.travelers))

        amount, currency = pick_price(seed, document_type=self.document_type)
        prices = [{"amount": amount, "currency": currency}]

        # Two QR codes when traveler count > 1 (boarding-pass-style); one
        # otherwise. Payloads are fake but visually plausible.
        qr_codes: list[str]
        if self.travelers == 1:
            qr_codes = [f"AIRTKT-{self.scenario_id}"]
        else:
            qr_codes = [
                f"AIRTKT-{self.scenario_id}-{i + 1:02d}" for i in range(self.travelers)
            ]

        return {
            "document_type": self.document_type,
            "cities": [origin.name, destination.name],
            "stations": stations,
            "accommodations": [],
            "venues": [],
            "travelers": traveler_names,
            "prices": prices,
            "qr_codes": qr_codes,
            "pdf_kind": "text",
            "scenario_id": self.scenario_id,
            "noise_seed": self.noise_seed,
        }


def _build_scenario_id(
    index: int,
    shape: Shape,
    travelers: int,
    origin: City,
    destination: City,
) -> str:
    """`NNN-air-<shape>-<pax>pax-<origin>-<destination>` slug.

    Stable across regenerations because `index` is a fixed function of the
    enumeration order.
    """
    shape_slug = "1leg" if shape == "one_leg" else "return"
    pax_slug = f"{travelers}pax"
    origin_slug = _slugify(origin.name)
    destination_slug = _slugify(destination.name)
    return f"{index:03d}-air-{shape_slug}-{pax_slug}-{origin_slug}-{destination_slug}"


def enumerate_scenarios() -> Iterator[ScenarioSpec]:
    """Yield the full ~25-scenario air-ticket matrix.

    Order: city-pair (outer) -> shape -> traveler count (inner). The order
    is fixed so the resulting indices in `scenario_id` are stable.
    Calling `list(enumerate_scenarios()) == list(enumerate_scenarios())`
    must always hold.
    """
    index = 1
    for origin_index, destination_index in CITY_PAIR_INDICES:
        origin = CITY_POOL[origin_index]
        destination = CITY_POOL[destination_index]
        for shape in SHAPES:
            for traveler_count in TRAVELER_COUNTS:
                scenario_id = _build_scenario_id(
                    index, shape, traveler_count, origin, destination
                )
                yield ScenarioSpec(
                    scenario_id=scenario_id,
                    document_type="air_ticket",
                    shape=shape,
                    travelers=traveler_count,
                    origin_index=origin_index,
                    destination_index=destination_index,
                    noise_seed=_noise_seed_for(scenario_id),
                )
                index += 1


# Sanity guard: the matrix must stay in the "~25" band (20..30 inclusive).
_TOTAL_SCENARIOS = len(CITY_PAIR_INDICES) * len(SHAPES) * len(TRAVELER_COUNTS)
if not 20 <= _TOTAL_SCENARIOS <= 30:  # pragma: no cover - guards configuration
    raise AssertionError(
        f"scenario count {_TOTAL_SCENARIOS} outside the [20, 30] band; "
        "rebalance CITY_PAIR_INDICES / SHAPES / TRAVELER_COUNTS"
    )


__all__ = [
    "ScenarioSpec",
    "Shape",
    "DocumentType",
    "SHAPES",
    "TRAVELER_COUNTS",
    "CITY_PAIR_INDICES",
    "enumerate_scenarios",
]
