"""Build the coverage matrix.

Sampling rule (kept simple on purpose):
- Iterate the cartesian product of (shape, pax, return, hotels, ordering).
- For each combination, emit ONE scenario at a shape-appropriate leg count
  picked deterministically from the scenario index.
- Mode mix is rotated so all three modes (air/bus/train) appear frequently.

This yields 3 (shapes) * 4 (pax) * 2 (return) * 2 (hotels) * 4 (orderings) = 192
scenarios. Each ordering appears with every shape and every pax count, so the
hardest axis (ordering) is densely covered.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

SHAPES = ("straight", "circle", "star")
PAX_COUNTS = (1, 2, 3, 4)
RETURN_FLAGS = (False, True)
HOTELS_FLAGS = (False, True)
ORDERINGS = ("forward", "reverse", "bisect", "seeded-shuffle")

LEG_COUNT_BY_SHAPE: dict[str, tuple[int, ...]] = {
    "straight": (2, 3, 4),
    "circle": (4, 5),
    "star": (4, 5, 6),
}

MODES: tuple[str, ...] = ("air", "bus", "train")


@dataclass(frozen=True, slots=True)
class ScenarioSpec:
    index: int
    shape: str
    pax: int
    return_trip: bool
    hotels: bool
    ordering: str
    leg_count: int
    primary_mode: str

    @property
    def slug(self) -> str:
        parts = [
            f"{self.index:03d}",
            self.shape,
            f"{self.pax}p",
            self.ordering,
        ]
        if self.return_trip:
            parts.append("return")
        if self.hotels:
            parts.append("hotels")
        return "-".join(parts)


def build_matrix() -> list[ScenarioSpec]:
    specs: list[ScenarioSpec] = []
    index = 0
    for shape in SHAPES:
        leg_choices = LEG_COUNT_BY_SHAPE[shape]
        for pax in PAX_COUNTS:
            for return_trip in RETURN_FLAGS:
                for hotels in HOTELS_FLAGS:
                    for ordering in ORDERINGS:
                        leg_count = leg_choices[index % len(leg_choices)]
                        mode = MODES[index % len(MODES)]
                        specs.append(
                            ScenarioSpec(
                                index=index,
                                shape=shape,
                                pax=pax,
                                return_trip=return_trip,
                                hotels=hotels,
                                ordering=ordering,
                                leg_count=leg_count,
                                primary_mode=mode,
                            )
                        )
                        index += 1
    return specs


def iter_matrix() -> Iterator[ScenarioSpec]:
    yield from build_matrix()
