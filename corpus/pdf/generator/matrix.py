"""Scenario matrix for the PDF corpus generator.

Slice 4 ships five document types across two structural shapes:

Transit tickets (air, rail, bus):

- ``shape`` axis: ``one_leg`` (origin -> destination, 2 stations) and
  ``return`` (origin -> destination + destination -> origin in one PDF; 3
  station entries where the destination is the layover carrying both an
  arrival and a departure datetime, and the origin appears twice as the
  outbound departure and the return arrival).
- ``travelers`` axis: 1 or 2 passenger names on the ticket.
- ``city_pair`` axis: 6 hand-curated origin/destination pairs sampled from
  the data layer's ``CITY_POOL`` so coverage isn't all Paris-Lisbon.

Each transit document_type contributes ``6 city_pairs * 2 shapes * 2
traveler_counts = 24`` scenarios.

Accommodation bookings (hotel, airbnb):

- ``stay_nights`` axis: short (2 nights) and long (5 nights).
- ``travelers`` axis: 1 or 2 guest names on the booking.
- ``city`` axis: the same 6 origin cities the transit pairs draw from, so
  coverage stays in the same geographical sample (Paris, Frankfurt, Madrid,
  London, Berlin, Prague).

Each accommodation document_type contributes ``6 cities * 2 stay_nights * 2
traveler_counts = 24`` scenarios.

Enumeration order is fixed: air first (IDs 001..024), then rail (025..048),
then bus (049..072), then hotel (073..096), then airbnb (097..120). The
committed Slice 3 air-ticket and Slice 4.1/4.2 rail+bus scenario IDs stay
byte-stable under this layout because new doc types are *appended*.

Supplementary document type is deferred to a later Slice 4 sub-task.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass, replace
from typing import Any, Literal

from corpus.pdf.generator.data import (
    CITY_POOL,
    City,
    pick_accommodation_dates,
    pick_datetime,
    pick_price,
    pick_transit_arrival,
    pick_travelers,
    pick_validity_window,
)

type Shape = Literal["one_leg", "return"]
type DocumentType = Literal[
    "air_ticket",
    "rail_ticket",
    "bus_ticket",
    "hotel_booking",
    "airbnb_booking",
    "supplementary",
]
type AccommodationKind = Literal["hotel", "airbnb"]
type VenueKind = Literal["sightseeing", "parking", "other"]
type Rendering = Literal["text", "rasterized"]

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

# Transit document types that ride the same axis structure (6 city-pairs *
# 2 shapes * 2 traveler counts = 24 scenarios each). Order matters: it
# dictates the overall index sequence used to build scenario_id slugs.
# Reordering here would re-slot every scenario_id, so leave air first to
# keep the committed 001..024 air scenarios byte-stable.
TRANSIT_DOCUMENT_TYPES: tuple[DocumentType, ...] = (
    "air_ticket",
    "rail_ticket",
    "bus_ticket",
)

# Accommodation booking variants follow the transit block in enumeration
# order, so their IDs start at 073 (24 transit * 3 + 1). Each contributes
# 6 cities * 2 stay_nights * 2 traveler counts = 24 scenarios.
ACCOMMODATION_DOCUMENT_TYPES: tuple[DocumentType, ...] = (
    "hotel_booking",
    "airbnb_booking",
)

# Stay length axis for accommodation bookings: short weekend vs. mid-week.
# Order matters for the scenario_id index — `(2, 5)` yields all 2-night
# scenarios first for a given city, matching the spec's
# `073-hotel-2nt-1pax-paris ... 096-hotel-5nt-2pax-prague` ordering.
ACCOMMODATION_NIGHT_COUNTS: tuple[int, ...] = (2, 5)

# Cities used by the accommodation document types. These intentionally line
# up with the origin city of each transit `CITY_PAIR_INDICES` entry so the
# corpus stays in the same geographical sample across doc types. Order
# matters: it drives the city order in scenario_id slugs (paris, frankfurt,
# madrid, london, berlin, prague).
ACCOMMODATION_CITY_INDICES: tuple[int, ...] = tuple(
    origin for origin, _ in CITY_PAIR_INDICES
)

# Supplementary cities are the same 6 origin cities used elsewhere. Order
# matters: it drives the city order in scenario_id slugs (paris, frankfurt,
# madrid, london, berlin, prague).
SUPPLEMENTARY_CITY_INDICES: tuple[int, ...] = ACCOMMODATION_CITY_INDICES

# Supplementary venue kinds — order is part of the scenario_id stable
# contract.
SUPPLEMENTARY_KINDS: tuple[VenueKind, ...] = ("sightseeing", "parking", "other")

# Supplementary axis: per city we yield 3 single-traveler scenarios (one per
# kind). The first 2 cities additionally yield 3 two-traveler scenarios (one
# per kind), giving exactly:
#
#   6 cities * 3 kinds * 1 traveler  = 18
# + 2 cities * 3 kinds * 1 traveler  =  6  (rendered as the 2pax variant)
#                                  = 24 total
#
# Per-kind count: each kind appears 6 (1pax) + 2 (2pax) = 8 times. Each kind
# is represented at least 4 times across the 6 cities, satisfying the slice
# coverage rule.
SUPPLEMENTARY_TWO_PAX_CITY_COUNT: int = 2

# Full document-type tuple for export. Order = enumeration order.
DOCUMENT_TYPES: tuple[DocumentType, ...] = (
    *TRANSIT_DOCUMENT_TYPES,
    *ACCOMMODATION_DOCUMENT_TYPES,
    "supplementary",
)

# Per-doc-type rasterized quota. Sum = 22, sits inside the [18, 28] band the
# functional spec calls "~15% rasterized" and gives each doc-type at least 3
# rasterized scenarios. Tuning the individual quotas (rather than picking a
# single global %) keeps the spread even across doc types — the maximum share
# of any one doc-type is 4/22 ≈ 18%, well under the 35% cap the slice plan
# calls for.
_RASTERIZED_QUOTA: dict[DocumentType, int] = {
    "air_ticket": 4,
    "rail_ticket": 4,
    "bus_ticket": 4,
    "hotel_booking": 3,
    "airbnb_booking": 3,
    "supplementary": 4,
}


# Per-document_type metadata for stations[] entries and scenario_id slugs.
# Keeping this in one table makes the air/rail/bus diff legible in code review.
@dataclass(frozen=True, slots=True)
class _DocumentMode:
    """Compile-time descriptor for one transit document_type."""

    slug: str
    station_kind: Literal["airport", "rail_station", "bus_terminal"]
    qr_prefix: str
    # Outbound + return departure/arrival local times (24h) and journey
    # durations. Picked to be realistic per mode and to ensure the return
    # leg lands later than the outbound leg's arrival.
    outbound_hour: int
    outbound_minute: int
    outbound_duration_hours: int
    outbound_duration_minutes: int
    return_hour: int
    return_minute: int
    return_duration_hours: int
    return_duration_minutes: int


# Air: existing Slice 3 values, preserved verbatim so committed JSON does not
# drift. The duration arithmetic reproduces 08:30->10:45 (2h15) outbound and
# 18:15->20:30 (2h15) return.
# Rail: ~3h door-to-door on typical European high-speed routes.
# Bus: ~6h on inter-city coach routes.
_DOCUMENT_MODES: dict[DocumentType, _DocumentMode] = {
    "air_ticket": _DocumentMode(
        slug="air",
        station_kind="airport",
        qr_prefix="AIRTKT",
        outbound_hour=8,
        outbound_minute=30,
        outbound_duration_hours=2,
        outbound_duration_minutes=15,
        return_hour=18,
        return_minute=15,
        return_duration_hours=2,
        return_duration_minutes=15,
    ),
    "rail_ticket": _DocumentMode(
        slug="rail",
        station_kind="rail_station",
        qr_prefix="RAILTKT",
        outbound_hour=7,
        outbound_minute=45,
        outbound_duration_hours=3,
        outbound_duration_minutes=10,
        return_hour=17,
        return_minute=20,
        return_duration_hours=3,
        return_duration_minutes=10,
    ),
    "bus_ticket": _DocumentMode(
        slug="bus",
        station_kind="bus_terminal",
        qr_prefix="BUSTKT",
        outbound_hour=6,
        outbound_minute=15,
        outbound_duration_hours=6,
        outbound_duration_minutes=30,
        return_hour=15,
        return_minute=45,
        return_duration_hours=6,
        return_duration_minutes=30,
    ),
}


# Per-accommodation-document_type metadata for accommodations[] entries and
# scenario_id slugs.
@dataclass(frozen=True, slots=True)
class _AccommodationMode:
    """Compile-time descriptor for one accommodation document_type."""

    slug: str
    accommodation_kind: AccommodationKind
    qr_prefix: str


_ACCOMMODATION_MODES: dict[DocumentType, _AccommodationMode] = {
    "hotel_booking": _AccommodationMode(
        slug="hotel",
        accommodation_kind="hotel",
        qr_prefix="HOTBKG",
    ),
    "airbnb_booking": _AccommodationMode(
        slug="airbnb",
        accommodation_kind="airbnb",
        qr_prefix="ABNB",
    ),
}


# Per-supplementary-kind metadata: scenario_id slug fragment and the city
# venue pool to pull the printed identifier from. ``day_offset`` anchors the
# validity-window helper at a stable point on the synthetic timeline; using
# the same offset across the three kinds is fine because the window shape is
# kind-specific.
@dataclass(frozen=True, slots=True)
class _SupplementaryMode:
    """Compile-time descriptor for one supplementary venue kind."""

    slug: str
    venue_kind: VenueKind
    qr_prefix: str


_SUPPLEMENTARY_MODES: dict[VenueKind, _SupplementaryMode] = {
    "sightseeing": _SupplementaryMode(
        slug="sight",
        venue_kind="sightseeing",
        qr_prefix="SUPP-SIGHT",
    ),
    "parking": _SupplementaryMode(
        slug="park",
        venue_kind="parking",
        qr_prefix="SUPP-PARK",
    ),
    "other": _SupplementaryMode(
        slug="other",
        venue_kind="other",
        qr_prefix="SUPP-OTHER",
    ),
}


def _identifier_for(city: City, document_type: DocumentType) -> str:
    """Return the printed station identifier for one ``(city, document_type)``."""
    match document_type:
        case "air_ticket":
            return city.iata
        case "rail_ticket":
            return city.rail_station
        case "bus_ticket":
            return city.bus_terminal
        case _:
            raise ValueError(
                f"_identifier_for() only handles transit document types, "
                f"got {document_type!r}"
            )


def _pick_property_identifier(
    city: City,
    document_type: DocumentType,
    seed: int,
) -> str:
    """Pick a deterministic property name from the city's pool.

    The choice is seeded from the scenario seed so the same scenario_id always
    yields the same printed property. Hotels draw from ``city.hotel_pool``;
    airbnbs draw from ``city.airbnb_pool``.
    """
    mode = _ACCOMMODATION_MODES[document_type]
    pool = city.hotel_pool if mode.accommodation_kind == "hotel" else city.airbnb_pool
    if not pool:
        raise ValueError(
            f"{city.name} has no {mode.accommodation_kind} pool defined; "
            "extend corpus/pdf/generator/data.CITY_POOL"
        )
    # Stable choice: hash the seed + accommodation kind so hotel and airbnb
    # don't accidentally pick the same index.
    digest = hashlib.sha256(
        f"{seed}:{mode.accommodation_kind}".encode("utf-8")
    ).digest()
    index = int.from_bytes(digest[:4], "big") % len(pool)
    return pool[index]


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
    """One generated scenario across any document type.

    `scenario_id` is the canonical stable identity. `expected_fields()` is the
    payload that becomes `expected-fields.json` once rendering lands.

    Transit scenarios (air / rail / bus) carry ``shape``, ``origin_index``,
    and ``destination_index``. Accommodation scenarios (hotel / airbnb) carry
    ``stay_nights`` and reuse ``origin_index`` as the property's city
    (``destination_index`` is set equal to ``origin_index`` for those rows).
    ``rendering`` is set by :func:`enumerate_scenarios` after the per-doc-type
    rasterized selection is computed; it drives both ``render.render_pdf``'s
    rasterize branch and the ``pdf_kind`` value baked into ``expected_fields``.
    The default values keep the existing transit call-sites working
    unchanged.
    """

    scenario_id: str
    document_type: DocumentType
    travelers: int
    origin_index: int
    destination_index: int
    noise_seed: int
    shape: Shape = "one_leg"
    stay_nights: int = 0
    venue_kind: VenueKind | None = None
    rendering: Rendering = "text"

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
        if self.document_type == "supplementary":
            return self._expected_fields_supplementary()
        if self.document_type in _ACCOMMODATION_MODES:
            return self._expected_fields_accommodation()
        return self._expected_fields_transit()

    def _expected_fields_transit(self) -> dict[str, Any]:
        """Build payload for air / rail / bus tickets."""
        origin, destination = self._resolve_cities()
        seed = _scenario_seed_for(self.scenario_id)
        mode = _DOCUMENT_MODES[self.document_type]

        outbound_departure = pick_datetime(
            seed,
            day_offset=10,
            hour=mode.outbound_hour,
            minute=mode.outbound_minute,
        )
        outbound_arrival = pick_transit_arrival(
            seed,
            day_offset=10,
            departure_hour=mode.outbound_hour,
            departure_minute=mode.outbound_minute,
            duration_hours=mode.outbound_duration_hours,
            duration_minutes=mode.outbound_duration_minutes,
        )

        origin_identifier = _identifier_for(origin, self.document_type)
        destination_identifier = _identifier_for(destination, self.document_type)
        station_kind = mode.station_kind

        stations: list[dict[str, Any]]
        if self.shape == "one_leg":
            stations = [
                {
                    "city": origin.name,
                    "kind": station_kind,
                    "identifier": origin_identifier,
                    "departure_datetime": outbound_departure,
                },
                {
                    "city": destination.name,
                    "kind": station_kind,
                    "identifier": destination_identifier,
                    "arrival_datetime": outbound_arrival,
                },
            ]
        else:  # "return"
            return_departure = pick_datetime(
                seed,
                day_offset=14,
                hour=mode.return_hour,
                minute=mode.return_minute,
            )
            return_arrival = pick_transit_arrival(
                seed,
                day_offset=14,
                departure_hour=mode.return_hour,
                departure_minute=mode.return_minute,
                duration_hours=mode.return_duration_hours,
                duration_minutes=mode.return_duration_minutes,
            )
            stations = [
                {
                    "city": origin.name,
                    "kind": station_kind,
                    "identifier": origin_identifier,
                    "departure_datetime": outbound_departure,
                },
                {
                    "city": destination.name,
                    "kind": station_kind,
                    "identifier": destination_identifier,
                    "arrival_datetime": outbound_arrival,
                    "departure_datetime": return_departure,
                },
                {
                    "city": origin.name,
                    "kind": station_kind,
                    "identifier": origin_identifier,
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
            qr_codes = [f"{mode.qr_prefix}-{self.scenario_id}"]
        else:
            qr_codes = [
                f"{mode.qr_prefix}-{self.scenario_id}-{i + 1:02d}"
                for i in range(self.travelers)
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
            "pdf_kind": self.rendering,
            "scenario_id": self.scenario_id,
            "noise_seed": self.noise_seed,
        }

    def _expected_fields_accommodation(self) -> dict[str, Any]:
        """Build payload for hotel / airbnb bookings."""
        city = CITY_POOL[self.origin_index]
        seed = _scenario_seed_for(self.scenario_id)
        mode = _ACCOMMODATION_MODES[self.document_type]

        # Anchor stays at day_offset=12 (mid-trip slot) — chosen to sit between
        # the transit-ticket outbound (day_offset=10) and return (day_offset=14)
        # anchors so a future "trip" framing could lay them out coherently.
        check_in, check_out = pick_accommodation_dates(
            seed,
            day_offset=12,
            nights=self.stay_nights,
        )
        identifier = _pick_property_identifier(city, self.document_type, seed)

        accommodations = [
            {
                "city": city.name,
                "kind": mode.accommodation_kind,
                "identifier": identifier,
                "check_in_datetime": check_in,
                "check_out_datetime": check_out,
            }
        ]

        traveler_names = list(pick_travelers(seed, self.travelers))

        amount, currency = pick_price(seed, document_type=self.document_type)
        prices = [{"amount": amount, "currency": currency}]

        # One booking reference QR — accommodations don't have per-guest
        # boarding-pass-style codes the way transit tickets do.
        qr_codes = [f"{mode.qr_prefix}-{self.scenario_id}"]

        return {
            "document_type": self.document_type,
            "cities": [city.name],
            "stations": [],
            "accommodations": accommodations,
            "venues": [],
            "travelers": traveler_names,
            "prices": prices,
            "qr_codes": qr_codes,
            "pdf_kind": self.rendering,
            "scenario_id": self.scenario_id,
            "noise_seed": self.noise_seed,
        }

    def _expected_fields_supplementary(self) -> dict[str, Any]:
        """Build payload for supplementary documents (sightseeing/parking/other)."""
        if self.venue_kind is None:
            raise ValueError(
                f"supplementary scenario {self.scenario_id!r} missing venue_kind"
            )
        city = CITY_POOL[self.origin_index]
        seed = _scenario_seed_for(self.scenario_id)
        mode = _SUPPLEMENTARY_MODES[self.venue_kind]

        identifier = _pick_venue_identifier(city, self.venue_kind, seed)
        # Anchor venue validity at day_offset=11 (just after the transit
        # outbound at day 10) so a future "trip" framing could sit a
        # sightseeing slot in the middle of a hotel stay coherently.
        valid_from, valid_to = pick_validity_window(
            seed, day_offset=11, kind=self.venue_kind
        )

        venues = [
            {
                "city": city.name,
                "kind": self.venue_kind,
                "identifier": identifier,
                "valid_from_datetime": valid_from,
                "valid_to_datetime": valid_to,
            }
        ]

        traveler_names = list(pick_travelers(seed, self.travelers))

        amount, currency = pick_price(seed, document_type=self.document_type)
        prices = [{"amount": amount, "currency": currency}]

        # One reference QR — supplementary docs don't have per-guest tokens
        # the way transit boarding passes do.
        qr_codes = [f"{mode.qr_prefix}-{self.scenario_id}"]

        return {
            "document_type": self.document_type,
            "cities": [city.name],
            "stations": [],
            "accommodations": [],
            "venues": venues,
            "travelers": traveler_names,
            "prices": prices,
            "qr_codes": qr_codes,
            "pdf_kind": self.rendering,
            "scenario_id": self.scenario_id,
            "noise_seed": self.noise_seed,
        }


def _pick_venue_identifier(
    city: City,
    venue_kind: VenueKind,
    seed: int,
) -> str:
    """Pick a deterministic venue name from the city's per-kind pool."""
    pool: tuple[str, ...]
    match venue_kind:
        case "sightseeing":
            pool = city.sightseeing_pool
        case "parking":
            pool = city.parking_pool
        case "other":
            pool = city.other_pool
    if not pool:
        raise ValueError(
            f"{city.name} has no {venue_kind} pool defined; "
            "extend corpus/pdf/generator/data.CITY_POOL"
        )
    # Stable choice: hash the seed + venue kind so the three kinds in the
    # same city don't accidentally pick the same index slot.
    digest = hashlib.sha256(f"{seed}:{venue_kind}".encode("utf-8")).digest()
    index = int.from_bytes(digest[:4], "big") % len(pool)
    return pool[index]


def _build_transit_scenario_id(
    index: int,
    document_type: DocumentType,
    shape: Shape,
    travelers: int,
    origin: City,
    destination: City,
) -> str:
    """`NNN-<mode>-<shape>-<pax>pax-<origin>-<destination>` slug.

    ``<mode>`` is the short slug carried by ``_DocumentMode`` (``air`` /
    ``rail`` / ``bus``). Stable across regenerations because ``index`` is a
    fixed function of the enumeration order in :func:`enumerate_scenarios`.
    """
    shape_slug = "1leg" if shape == "one_leg" else "return"
    pax_slug = f"{travelers}pax"
    mode_slug = _DOCUMENT_MODES[document_type].slug
    origin_slug = _slugify(origin.name)
    destination_slug = _slugify(destination.name)
    return (
        f"{index:03d}-{mode_slug}-{shape_slug}-{pax_slug}-"
        f"{origin_slug}-{destination_slug}"
    )


def _build_accommodation_scenario_id(
    index: int,
    document_type: DocumentType,
    nights: int,
    travelers: int,
    city: City,
) -> str:
    """`NNN-<mode>-<nights>nt-<pax>pax-<city>` slug.

    ``<mode>`` is the short slug carried by ``_AccommodationMode`` (``hotel``
    / ``airbnb``). Stable across regenerations because ``index`` is a fixed
    function of the enumeration order in :func:`enumerate_scenarios`.
    """
    mode_slug = _ACCOMMODATION_MODES[document_type].slug
    city_slug = _slugify(city.name)
    return f"{index:03d}-{mode_slug}-{nights}nt-{travelers}pax-{city_slug}"


def _build_supplementary_scenario_id(
    index: int,
    venue_kind: VenueKind,
    travelers: int,
    city: City,
) -> str:
    """`NNN-supp-<kind>-<pax>pax-<city>` slug.

    Stable across regenerations because ``index`` is a fixed function of the
    enumeration order in :func:`enumerate_scenarios`.
    """
    kind_slug = _SUPPLEMENTARY_MODES[venue_kind].slug
    city_slug = _slugify(city.name)
    pax_slug = f"{travelers}pax"
    return f"{index:03d}-supp-{kind_slug}-{pax_slug}-{city_slug}"


def _enumerate_transit_scenarios(start_index: int) -> Iterator[ScenarioSpec]:
    """Yield transit-ticket scenarios. Starts the index counter at ``start_index``."""
    index = start_index
    for document_type in TRANSIT_DOCUMENT_TYPES:
        for origin_index, destination_index in CITY_PAIR_INDICES:
            origin = CITY_POOL[origin_index]
            destination = CITY_POOL[destination_index]
            for shape in SHAPES:
                for traveler_count in TRAVELER_COUNTS:
                    scenario_id = _build_transit_scenario_id(
                        index,
                        document_type,
                        shape,
                        traveler_count,
                        origin,
                        destination,
                    )
                    yield ScenarioSpec(
                        scenario_id=scenario_id,
                        document_type=document_type,
                        shape=shape,
                        travelers=traveler_count,
                        origin_index=origin_index,
                        destination_index=destination_index,
                        noise_seed=_noise_seed_for(scenario_id),
                    )
                    index += 1


def _enumerate_accommodation_scenarios(start_index: int) -> Iterator[ScenarioSpec]:
    """Yield hotel/airbnb scenarios. Starts the index counter at ``start_index``.

    Order: document_type (outer) -> city -> stay_nights -> traveler count
    (inner). City order = ``ACCOMMODATION_CITY_INDICES``, which matches the
    origin city of each transit ``CITY_PAIR_INDICES`` row.
    """
    index = start_index
    for document_type in ACCOMMODATION_DOCUMENT_TYPES:
        for city_index in ACCOMMODATION_CITY_INDICES:
            city = CITY_POOL[city_index]
            for nights in ACCOMMODATION_NIGHT_COUNTS:
                for traveler_count in TRAVELER_COUNTS:
                    scenario_id = _build_accommodation_scenario_id(
                        index,
                        document_type,
                        nights,
                        traveler_count,
                        city,
                    )
                    yield ScenarioSpec(
                        scenario_id=scenario_id,
                        document_type=document_type,
                        # ``shape`` is irrelevant for accommodations; the
                        # default ``one_leg`` is harmless because the
                        # expected_fields() dispatcher branches on doc type
                        # before reading ``shape``.
                        travelers=traveler_count,
                        origin_index=city_index,
                        destination_index=city_index,
                        noise_seed=_noise_seed_for(scenario_id),
                        stay_nights=nights,
                    )
                    index += 1


def _enumerate_supplementary_scenarios(start_index: int) -> Iterator[ScenarioSpec]:
    """Yield supplementary scenarios. Starts the index counter at ``start_index``.

    Order: traveler-count (outer) -> city -> venue kind (inner). The first
    pass is the 1pax block (6 cities * 3 kinds = 18 scenarios), the second
    pass is the 2pax block for the first
    ``SUPPLEMENTARY_TWO_PAX_CITY_COUNT`` cities * 3 kinds = 6 scenarios.
    Appending the 2pax block after the 1pax block keeps the 1pax IDs stable
    if the 2pax axis ever grows.
    """
    index = start_index
    # 1pax pass: every city, every kind.
    for city_index in SUPPLEMENTARY_CITY_INDICES:
        city = CITY_POOL[city_index]
        for venue_kind in SUPPLEMENTARY_KINDS:
            scenario_id = _build_supplementary_scenario_id(
                index, venue_kind, travelers=1, city=city
            )
            yield ScenarioSpec(
                scenario_id=scenario_id,
                document_type="supplementary",
                travelers=1,
                origin_index=city_index,
                destination_index=city_index,
                noise_seed=_noise_seed_for(scenario_id),
                venue_kind=venue_kind,
            )
            index += 1
    # 2pax pass: first N cities, every kind.
    for city_index in SUPPLEMENTARY_CITY_INDICES[:SUPPLEMENTARY_TWO_PAX_CITY_COUNT]:
        city = CITY_POOL[city_index]
        for venue_kind in SUPPLEMENTARY_KINDS:
            scenario_id = _build_supplementary_scenario_id(
                index, venue_kind, travelers=2, city=city
            )
            yield ScenarioSpec(
                scenario_id=scenario_id,
                document_type="supplementary",
                travelers=2,
                origin_index=city_index,
                destination_index=city_index,
                noise_seed=_noise_seed_for(scenario_id),
                venue_kind=venue_kind,
            )
            index += 1


def _rasterized_rank(scenario_id: str) -> int:
    """Stable per-scenario rank used to pick the rasterized subset.

    SHA-256 of ``"rasterize:" + scenario_id``, first 8 bytes interpreted as a
    big-endian unsigned integer. Sorting by this value then taking the lowest
    N per doc-type gives a deterministic, spread-by-doc-type selection that
    is reproducible from the scenario_id alone — no extra state needed.

    Prefixing the hash input keeps this seed independent of the data and
    noise seeds so any future tweak to those stays orthogonal to the
    rasterized membership set.
    """
    digest = hashlib.sha256(("rasterize:" + scenario_id).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _select_rasterized_ids(specs: list[ScenarioSpec]) -> frozenset[str]:
    """Pick the rasterized subset deterministically, per-doc-type quota.

    Groups ``specs`` by ``document_type``, ranks each group's scenario_ids by
    :func:`_rasterized_rank`, and takes the lowest ``_RASTERIZED_QUOTA[type]``
    per group. Stable across regenerations because the rank is a pure
    function of the scenario_id string.
    """
    selected: set[str] = set()
    for document_type, quota in _RASTERIZED_QUOTA.items():
        group = [s for s in specs if s.document_type == document_type]
        ranked = sorted(group, key=lambda s: _rasterized_rank(s.scenario_id))
        selected.update(s.scenario_id for s in ranked[:quota])
    return frozenset(selected)


def _enumerate_all_text() -> Iterator[ScenarioSpec]:
    """Yield every scenario with the default ``rendering="text"`` flag.

    Internal helper used by :func:`enumerate_scenarios` to materialise the
    full matrix once so the rasterized selection can be computed against the
    real scenario_ids.
    """
    yield from _enumerate_transit_scenarios(start_index=1)
    transit_total = (
        len(TRANSIT_DOCUMENT_TYPES)
        * len(CITY_PAIR_INDICES)
        * len(SHAPES)
        * len(TRAVELER_COUNTS)
    )
    yield from _enumerate_accommodation_scenarios(start_index=transit_total + 1)
    accommodation_total = len(ACCOMMODATION_DOCUMENT_TYPES) * (
        len(ACCOMMODATION_CITY_INDICES)
        * len(ACCOMMODATION_NIGHT_COUNTS)
        * len(TRAVELER_COUNTS)
    )
    yield from _enumerate_supplementary_scenarios(
        start_index=transit_total + accommodation_total + 1
    )


def enumerate_scenarios() -> Iterator[ScenarioSpec]:
    """Yield the full scenario matrix across every Slice 4 document type.

    Order is fixed so scenario_id indices are stable:

    - Transit: air (001..024) -> rail (025..048) -> bus (049..072), grouped
      by city-pair -> shape -> traveler count.
    - Accommodation: hotel (073..096) -> airbnb (097..120), grouped by
      city -> stay_nights -> traveler count.
    - Supplementary: 121..144 — 18 single-traveler (one per city per kind)
      then 6 two-traveler (first 2 cities per kind).

    Each spec also carries a ``rendering`` flag of ``"text"`` (default) or
    ``"rasterized"`` (~15% of the corpus, ``_RASTERIZED_QUOTA`` per doc-type).
    The rasterized subset is deterministic per scenario_id, so
    ``list(enumerate_scenarios()) == list(enumerate_scenarios())`` always
    holds. Text-only callers can ignore the flag — the JSON payload's
    ``pdf_kind`` mirrors it automatically.
    """
    all_specs = list(_enumerate_all_text())
    rasterized_ids = _select_rasterized_ids(all_specs)
    for spec in all_specs:
        if spec.scenario_id in rasterized_ids:
            yield replace(spec, rendering="rasterized")
        else:
            yield spec


# Sanity guards: each document_type's axis sweep must stay at "~25" (20..30
# inclusive) so the overall corpus stays evenly weighted across types.
_PER_TRANSIT_DOC_SCENARIOS = len(CITY_PAIR_INDICES) * len(SHAPES) * len(TRAVELER_COUNTS)
_PER_ACCOMMODATION_DOC_SCENARIOS = (
    len(ACCOMMODATION_CITY_INDICES)
    * len(ACCOMMODATION_NIGHT_COUNTS)
    * len(TRAVELER_COUNTS)
)
_PER_SUPPLEMENTARY_DOC_SCENARIOS = (
    len(SUPPLEMENTARY_CITY_INDICES) * len(SUPPLEMENTARY_KINDS)
    + SUPPLEMENTARY_TWO_PAX_CITY_COUNT * len(SUPPLEMENTARY_KINDS)
)
for _per_doc in (
    _PER_TRANSIT_DOC_SCENARIOS,
    _PER_ACCOMMODATION_DOC_SCENARIOS,
    _PER_SUPPLEMENTARY_DOC_SCENARIOS,
):
    if not 20 <= _per_doc <= 30:  # pragma: no cover - guards configuration
        raise AssertionError(
            f"per-doc scenario count {_per_doc} outside the [20, 30] band; "
            "rebalance the axis tuples"
        )

_TOTAL_SCENARIOS = (
    _PER_TRANSIT_DOC_SCENARIOS * len(TRANSIT_DOCUMENT_TYPES)
    + _PER_ACCOMMODATION_DOC_SCENARIOS * len(ACCOMMODATION_DOCUMENT_TYPES)
    + _PER_SUPPLEMENTARY_DOC_SCENARIOS
)


__all__ = [
    "ScenarioSpec",
    "Shape",
    "DocumentType",
    "AccommodationKind",
    "VenueKind",
    "Rendering",
    "DOCUMENT_TYPES",
    "TRANSIT_DOCUMENT_TYPES",
    "ACCOMMODATION_DOCUMENT_TYPES",
    "SHAPES",
    "TRAVELER_COUNTS",
    "ACCOMMODATION_CITY_INDICES",
    "ACCOMMODATION_NIGHT_COUNTS",
    "CITY_PAIR_INDICES",
    "SUPPLEMENTARY_CITY_INDICES",
    "SUPPLEMENTARY_KINDS",
    "SUPPLEMENTARY_TWO_PAX_CITY_COUNT",
    "enumerate_scenarios",
]
