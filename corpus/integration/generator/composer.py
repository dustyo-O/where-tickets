"""Compose a list of primitives into a :class:`TripBundle`.

A :class:`TripBundle` carries everything needed to write one integration trip:

- ``manifest`` — dict to be serialised as ``corpus/integration/<slug>/manifest.json``.
- ``expected_route`` — dict to be serialised as
  ``corpus/integration/<slug>/expected-route.json``.
- ``pdfs`` — ordered list of ``(pdf_relpath, template_name, render_context,
  expected_fields_dict, rendering)`` tuples. The CLI walks this list to render
  PDFs into ``corpus/pdf/layer2/<slug>/`` and write each sibling
  ``<NN>-<docname>.expected-fields.json`` alongside.

The composer derives the expected route by tracking chronological events per
primitive:

- Transit primitive → one event per chronologically-paired leg, contributing a
  CREATE-or-ENRICH stop at the from-city + a CREATE-or-ENRICH stop at the
  to-city, plus a transit between them.
- Accommodation primitive → one stop creation/enrichment at the city, plus an
  accommodation entry pinned there.
- Supplementary-with-venue primitive → one stop creation/enrichment at the city,
  plus a venue entry pinned there.
- Supplementary-no-location primitive → one ``unattachedDocuments`` entry.

Same-city stops collapse via :func:`spikes.route_engine_llm.models.city_identity`
(``strip().casefold()``). The composer mirrors what the engine does, so the
expected route equals the engine's eventual ``WorkingRoute`` projection (modulo
the engine's internal fields like ``stop.stations[]`` and engine-owned IDs,
which ``scoring.final_route_match`` strips before comparison).

Determinism: the composer takes no random state and returns dicts ordered by
the primitives' chronology + insertion. Two calls with the same :class:`TripSpec`
return byte-identical ``manifest`` / ``expected_route`` / per-PDF
``expected_fields``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from corpus.integration.generator import primitives as p

__all__ = [
    "ComposerError",
    "PDFEntry",
    "TripBundle",
    "TripSpec",
    "compose_trip",
]


class ComposerError(Exception):
    """Raised when the composer detects a malformed :class:`TripSpec`.

    Distinct from :class:`ValueError` so the CLI can surface composer-level
    misconfigurations with a clean error frame.
    """


@dataclass(frozen=True, slots=True)
class TripSpec:
    """The catalogue's input to the composer.

    ``slug`` is the trip's directory name under both
    ``corpus/pdf/layer2/<slug>/`` and ``corpus/integration/<slug>/``.
    ``travelers`` is the full set of travelers on the trip — every primitive's
    ``travelers`` tuple must be a subset of this set (composer-checked).
    ``notes`` is copied verbatim into ``manifest.notes`` for human eyes.
    ``primitives`` is the ordered list of per-PDF building blocks.
    """

    slug: str
    travelers: tuple[str, ...]
    primitives: tuple[p.AnyPrimitive, ...]
    notes: str | None = None


@dataclass(slots=True)
class PDFEntry:
    """One PDF the CLI must emit for a trip.

    ``relpath`` is RELATIVE to ``corpus/pdf/layer2/`` (e.g.
    ``"01-air-out-hotel-back-paris-lisbon-1pax/01-air-out.pdf"``). The manifest
    encodes the same string prefixed with ``"layer2/"``.

    ``expected_fields`` is the dict that becomes
    ``corpus/pdf/layer2/<slug>/<NN>-<docname>.expected-fields.json``; the layer-2
    PDF runner discovers and asserts it for free.

    ``render_template`` and ``render_context`` are passed to
    :func:`corpus.pdf.generator.render.render_pdf` directly.

    ``expect_unreadable`` is True only for :class:`primitives.UnreadablePrimitive`
    entries — the CLI emits a blank PDF for them and the manifest sets the
    ``expect_unreadable`` flag on the entry.
    """

    relpath: str
    render_template: str | None
    render_context: dict[str, Any] | None
    expected_fields: dict[str, Any] | None
    rendering: p.Rendering
    expect_unreadable: bool = False


@dataclass(slots=True)
class TripBundle:
    """Everything :mod:`__main__` writes to disk for one trip."""

    slug: str
    manifest: dict[str, Any]
    expected_route: dict[str, Any]
    pdfs: list[PDFEntry] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Constants / helpers
# --------------------------------------------------------------------------- #


_TEMPLATE_BY_TRANSIT_MODE: dict[p.TransitMode, str] = {
    "air": "air-ticket.html.j2",
    "rail": "rail-ticket.html.j2",
    "bus": "bus-ticket.html.j2",
}

_TEMPLATE_BY_ACCOMMODATION_KIND: dict[str, str] = {
    "hotel": "hotel-booking.html.j2",
    "airbnb": "airbnb-booking.html.j2",
}

_SUPPLEMENTARY_TEMPLATE = "supplementary.html.j2"


def _city_identity(name: str) -> str:
    """Mirror :func:`spikes.route_engine_llm.models.city_identity`.

    Strips whitespace and case-folds the city name so two stops with the same
    printed city collapse to one. Duplicated here so the generator package
    stays free of the ``spikes`` import (the generator runs in the ``corpus``
    venv per ``regen-integration-corpus``).
    """
    return name.strip().casefold()


def _split_iso(value: datetime) -> tuple[str, str]:
    """Split a UTC datetime into ``("YYYY-MM-DD", "HH:MM")`` for templates."""
    return value.strftime("%Y-%m-%d"), value.strftime("%H:%M")


def _iso_local(value: datetime) -> str:
    """Return ``YYYY-MM-DDTHH:MM:SS`` (no tz) for the per-PDF JSON."""
    return value.strftime("%Y-%m-%dT%H:%M:%S")


def _iso_z(value: datetime) -> str:
    """Return ``YYYY-MM-DDTHH:MM:SSZ`` for the expected-route JSON."""
    # The engine's `_stamp` calls `astimezone(UTC).isoformat()`; for tz-aware
    # UTC datetimes that returns ``...+00:00``. We canonicalise to ``...Z``
    # for readability and rely on Pydantic + scoring to normalise both forms
    # when comparing.
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_travelers(travelers: Iterable[str]) -> list[str]:
    """Return the traveler list in stable order (input order, dedup)."""
    seen: list[str] = []
    for name in travelers:
        if name not in seen:
            seen.append(name)
    return seen


# --------------------------------------------------------------------------- #
# Per-PDF expected-fields + render context
# --------------------------------------------------------------------------- #


def _build_transit_expected_fields(
    primitive: p.TransitPrimitive,
    *,
    scenario_id: str,
) -> dict[str, Any]:
    """Build the ``expected-fields.json`` payload for one transit PDF.

    Mirrors :meth:`corpus.pdf.generator.matrix.ScenarioSpec._expected_fields_transit`'s
    shape so the layer-2 PDF runner validates it against the same schema.
    """
    stations_payload: list[dict[str, Any]] = []
    for station in primitive.stations:
        entry: dict[str, Any] = {
            "city": station.city,
            "kind": primitive.station_kind,
            "identifier": station.identifier,
        }
        if station.departure_at is not None:
            entry["departure_datetime"] = _iso_local(station.departure_at)
        if station.arrival_at is not None:
            entry["arrival_datetime"] = _iso_local(station.arrival_at)
        stations_payload.append(entry)

    qr_codes = [f"{primitive.qr_prefix}-{scenario_id}-{i + 1:02d}" for i in range(len(primitive.travelers))]
    return {
        "document_type": primitive.document_type,
        "cities": list(primitive.cities),
        "stations": stations_payload,
        "accommodations": [],
        "venues": [],
        "travelers": list(primitive.travelers),
        "prices": [{"amount": round(primitive.price_eur, 2), "currency": "EUR"}],
        "qr_codes": qr_codes,
        "pdf_kind": primitive.rendering,
        "scenario_id": scenario_id,
    }


def _build_transit_render_context(
    primitive: p.TransitPrimitive,
    fields: dict[str, Any],
) -> dict[str, Any]:
    """Build the Jinja2 context for the transit-ticket template.

    Mirrors :func:`corpus.pdf.generator.__main__._build_context`'s transit
    branch: paired chronological legs from ``stations[]``, plus the
    full ``ExtractedFields`` payload + a per-PDF :class:`NoiseChoices`.
    """
    noise = _pick_noise(str(fields["scenario_id"]))
    from corpus.pdf.generator.noise import MARKETING_BANNERS  # noqa: PLC0415

    banners = list(MARKETING_BANNERS[: noise.marketing_banner_count])
    legs: list[dict[str, str]] = []
    stations = list(fields["stations"])
    for i in range(len(stations) - 1):
        origin = stations[i]
        destination = stations[i + 1]
        if "departure_datetime" not in origin or "arrival_datetime" not in destination:
            continue
        dep_date, dep_time = origin["departure_datetime"].split("T")
        arr_date, arr_time = destination["arrival_datetime"].split("T")
        legs.append(
            {
                "origin_city": str(origin["city"]),
                "origin_identifier": str(origin["identifier"]),
                "origin_iata": str(origin["identifier"]),
                "destination_city": str(destination["city"]),
                "destination_identifier": str(destination["identifier"]),
                "destination_iata": str(destination["identifier"]),
                "departure_date": dep_date,
                "departure_time": dep_time[:5],
                "arrival_date": arr_date,
                "arrival_time": arr_time[:5],
            }
        )
    if not legs:
        msg = f"transit primitive produced zero legs (cities={primitive.cities!r})"
        raise ComposerError(msg)
    return {
        "data": fields,
        "noise": noise,
        "banners": banners,
        "tc_block": noise.tc_block,
        "footer_variant": noise.footer_variant,
        "qr_codes": list(fields.get("qr_codes", [])),
        "legs": legs,
    }


def _build_accommodation_expected_fields(
    primitive: p.AccommodationPrimitive,
    *,
    scenario_id: str,
) -> dict[str, Any]:
    """Build the ``expected-fields.json`` payload for one accommodation PDF."""
    accommodations_payload = [
        {
            "city": primitive.city,
            "kind": primitive.kind,
            "identifier": primitive.identifier,
            "check_in_datetime": _iso_local(primitive.check_in_at),
            "check_out_datetime": _iso_local(primitive.check_out_at),
        }
    ]
    return {
        "document_type": primitive.document_type,
        "cities": [primitive.city],
        "stations": [],
        "accommodations": accommodations_payload,
        "venues": [],
        "travelers": list(primitive.travelers),
        "prices": [{"amount": round(primitive.price_eur, 2), "currency": "EUR"}],
        "qr_codes": [f"{primitive.qr_prefix}-{scenario_id}"],
        "pdf_kind": primitive.rendering,
        "scenario_id": scenario_id,
    }


def _build_accommodation_render_context(
    primitive: p.AccommodationPrimitive,
    fields: dict[str, Any],
) -> dict[str, Any]:
    """Build the Jinja2 context for the hotel / airbnb template."""
    noise = _pick_noise(str(fields["scenario_id"]))
    from corpus.pdf.generator.noise import MARKETING_BANNERS  # noqa: PLC0415

    banners = list(MARKETING_BANNERS[: noise.marketing_banner_count])
    stays: list[dict[str, str]] = []
    for entry in fields["accommodations"]:
        in_date, in_time = entry["check_in_datetime"].split("T")
        out_date, out_time = entry["check_out_datetime"].split("T")
        stays.append(
            {
                "city": str(entry["city"]),
                "kind": str(entry["kind"]),
                "identifier": str(entry["identifier"]),
                "check_in_date": in_date,
                "check_in_time": in_time[:5],
                "check_out_date": out_date,
                "check_out_time": out_time[:5],
            }
        )
    return {
        "data": fields,
        "noise": noise,
        "banners": banners,
        "tc_block": noise.tc_block,
        "footer_variant": noise.footer_variant,
        "qr_codes": list(fields.get("qr_codes", [])),
        "stays": stays,
    }


def _build_supplementary_expected_fields(
    primitive: p.SupplementaryPrimitive,
    *,
    scenario_id: str,
) -> dict[str, Any]:
    """Build the ``expected-fields.json`` payload for one supplementary PDF.

    With-venue: ``cities`` and ``venues`` carry the venue's city.
    No-location: every routable list is empty and ``cities`` carries one
    placeholder city string only when the schema requires it. The schema's
    ``cities`` field has ``minItems: 1``, so a no-location supplementary STILL
    needs at least one city printed on the document — we use the first
    traveler's first name as a harmless placeholder header in the PDF, and
    the JSON's ``cities`` carries it verbatim. The engine ignores ``cities[]``
    when classifying a supplementary-no-location event.
    """
    if primitive.venue_kind is not None:
        assert primitive.venue_city is not None  # noqa: S101 — narrow Optional for type-checker
        assert primitive.venue_identifier is not None  # noqa: S101
        venue: dict[str, Any] = {
            "city": primitive.venue_city,
            "kind": primitive.venue_kind,
            "identifier": primitive.venue_identifier,
        }
        if primitive.valid_from_at is not None:
            venue["valid_from_datetime"] = _iso_local(primitive.valid_from_at)
        if primitive.valid_to_at is not None:
            venue["valid_to_datetime"] = _iso_local(primitive.valid_to_at)
        cities = [primitive.venue_city]
        venues_payload: list[dict[str, Any]] = [venue]
    else:
        # No-location supplementary — `cities[]` minItems=1 means we MUST
        # carry at least one city string on the document. Use a stable
        # synthetic placeholder ("General") that the engine doesn't try to
        # route on (supplementary without stations/accommodations/venues
        # bypasses city routing entirely).
        cities = ["General"]
        venues_payload = []
    return {
        "document_type": "supplementary",
        "cities": cities,
        "stations": [],
        "accommodations": [],
        "venues": venues_payload,
        "travelers": list(primitive.travelers),
        "prices": [{"amount": round(primitive.price_eur, 2), "currency": "EUR"}],
        "qr_codes": [f"{primitive.qr_prefix}-{scenario_id}"],
        "pdf_kind": primitive.rendering,
        "scenario_id": scenario_id,
    }


def _build_supplementary_render_context(
    primitive: p.SupplementaryPrimitive,
    fields: dict[str, Any],
) -> dict[str, Any]:
    """Build the Jinja2 context for the supplementary template.

    The template needs `venues[0]` (with date splits) + a `brand` block.
    For a no-location supplementary we synthesise a minimal "venue" entry so
    the template still renders — its city/identifier are the placeholders
    we baked into the expected-fields payload. The supplementary template
    branches on `brand`; we always pick the kind table that matches
    ``primitive.venue_kind`` (with `"other"` as the no-location fallback).
    """
    from corpus.pdf.generator.data import SUPPLEMENTARY_BRANDS  # local import — corpus venv only  # noqa: PLC0415
    from corpus.pdf.generator.noise import MARKETING_BANNERS  # noqa: PLC0415

    noise = _pick_noise(str(fields["scenario_id"]))
    banners = list(MARKETING_BANNERS[: noise.marketing_banner_count])

    if primitive.venue_kind is not None:
        rendered_venues: list[dict[str, str]] = []
        for entry in fields["venues"]:
            valid_from_date, valid_from_time = "", ""
            valid_to_date, valid_to_time = "", ""
            if "valid_from_datetime" in entry:
                d, t = entry["valid_from_datetime"].split("T")
                valid_from_date, valid_from_time = d, t[:5]
            if "valid_to_datetime" in entry:
                d, t = entry["valid_to_datetime"].split("T")
                valid_to_date, valid_to_time = d, t[:5]
            rendered_venues.append(
                {
                    "city": str(entry["city"]),
                    "kind": str(entry["kind"]),
                    "identifier": str(entry["identifier"]),
                    "valid_from_date": valid_from_date,
                    "valid_from_time": valid_from_time,
                    "valid_to_date": valid_to_date,
                    "valid_to_time": valid_to_time,
                }
            )
        brand = SUPPLEMENTARY_BRANDS[primitive.venue_kind]
    else:
        # No-location: synthesise one placeholder venue so the template still
        # has something to render. The expected-fields JSON has empty `venues`
        # — what the template prints does NOT have to match `venues[]` (only
        # `cities[]` does, and we pin that to "General").
        rendered_venues = [
            {
                "city": "General",
                "kind": "other",
                "identifier": primitive.reference_code,
                "valid_from_date": "",
                "valid_from_time": "",
                "valid_to_date": "",
                "valid_to_time": "",
            }
        ]
        brand = SUPPLEMENTARY_BRANDS["other"]

    return {
        "data": fields,
        "noise": noise,
        "banners": banners,
        "tc_block": noise.tc_block,
        "footer_variant": noise.footer_variant,
        "qr_codes": list(fields.get("qr_codes", [])),
        "venues": rendered_venues,
        "venue": rendered_venues[0],
        "brand": brand,
    }


# --------------------------------------------------------------------------- #
# Noise — reuse the layer-1 noise picker with a seed stable for each
# (slug, scenario_id) pair. Integration PDFs share the layer-1 templates so
# they need a real :class:`NoiseChoices`; deferring to ``pick_noise`` keeps
# the bundled CSS + partials available without duplicating the catalog here.
# --------------------------------------------------------------------------- #


def _noise_seed_for(scenario_id: str) -> int:
    """Stable per-PDF noise seed (SHA-256 of the scenario id)."""
    import hashlib  # noqa: PLC0415 — local import keeps this generator's hot path light

    digest = hashlib.sha256(("noise:" + scenario_id).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _pick_noise(scenario_id: str) -> Any:
    """Return a :class:`NoiseChoices` for the given scenario id."""
    from corpus.pdf.generator.noise import pick_noise  # noqa: PLC0415

    return pick_noise(_noise_seed_for(scenario_id))


# --------------------------------------------------------------------------- #
# Expected route derivation
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Leg:
    """One transit leg derived from a transit primitive's stations."""

    from_city: str
    to_city: str
    departure_at: datetime
    arrival_at: datetime
    mode: p.TransitMode
    travelers: list[str]
    source_document_id: str


def _legs_from_transit(
    primitive: p.TransitPrimitive,
    *,
    source_document_id: str,
) -> list[_Leg]:
    """Derive ordered legs from a transit primitive's stations.

    Mirrors :func:`spikes.route_engine_algorithmic.rules._legs_from_stations`'s
    strict ``dep → arr → dep → arr`` alternation when sorted chronologically.
    For a 2-station ticket: 1 leg. For a 3-station compact return: 2 legs.
    """
    stations = list(primitive.stations)
    if len(stations) < 2:
        msg = f"transit primitive needs >= 2 stations, got {len(stations)}"
        raise ComposerError(msg)

    # Sort station entries by their first non-None timestamp.
    def _key(entry: p._StationLeg) -> datetime:
        return entry.departure_at or entry.arrival_at or datetime.min

    stations_chrono = sorted(stations, key=_key)

    # Expand a layover/turnaround station (both arrival_at and departure_at)
    # into two atomic events: one arrival, one departure. Then pair
    # consecutive (departure, arrival) into legs.
    atoms: list[tuple[p._StationLeg, datetime, str]] = []  # (station, when, kind)
    for s in stations_chrono:
        if s.arrival_at is not None and s.departure_at is not None:
            atoms.append((s, s.arrival_at, "arr"))
            atoms.append((s, s.departure_at, "dep"))
        elif s.departure_at is not None:
            atoms.append((s, s.departure_at, "dep"))
        elif s.arrival_at is not None:
            atoms.append((s, s.arrival_at, "arr"))

    atoms.sort(key=lambda x: x[1])

    legs: list[_Leg] = []
    i = 0
    while i < len(atoms):
        dep_station, dep_when, dep_kind = atoms[i]
        if dep_kind != "dep":
            msg = (
                f"chronological station sequence does not start with a departure "
                f"at index {i}: {atoms!r}"
            )
            raise ComposerError(msg)
        if i + 1 >= len(atoms):
            msg = f"dangling departure with no matching arrival: {dep_station!r}"
            raise ComposerError(msg)
        arr_station, arr_when, arr_kind = atoms[i + 1]
        if arr_kind != "arr":
            msg = f"expected arrival after departure at index {i + 1}: {atoms!r}"
            raise ComposerError(msg)
        legs.append(
            _Leg(
                from_city=dep_station.city,
                to_city=arr_station.city,
                departure_at=dep_when,
                arrival_at=arr_when,
                mode=primitive.mode,
                travelers=list(primitive.travelers),
                source_document_id=source_document_id,
            )
        )
        i += 2
    return legs


@dataclass(slots=True)
class _StopState:
    """Mutable accumulator for one expected-route stop."""

    city: str  # the printed city (first-seen casing); the comparison key is `city_identity(city)`
    arrival_at: datetime | None = None
    departure_at: datetime | None = None
    travelers: list[str] = field(default_factory=list)
    accommodations: list[dict[str, Any]] = field(default_factory=list)
    venues: list[dict[str, Any]] = field(default_factory=list)
    # Anchor time used to compute insertion order across stops. For a stop
    # created by a transit's `from` city, the anchor is `departure_at`; for a
    # stop created by a transit's `to` city, the anchor is `arrival_at`; for
    # accommodation-only stops, it's `check_in_at`; for venue-only stops, it's
    # `valid_from_at` (or `valid_to_at`, or +inf if neither is set).
    anchor_at: datetime | None = None

    def merge_traveler(self, name: str) -> None:
        """Append `name` to ``travelers`` if not already present."""
        if name not in self.travelers:
            self.travelers.append(name)


@dataclass(slots=True)
class _RouteAccumulator:
    """In-flight state while walking primitives chronologically.

    The accumulator captures stops as the composer walks events, then emits
    them ordered by ``anchor_at`` (or insertion order on ties). Same-city
    sequential events merge into the same stop; same-city NON-sequential
    events (e.g. Paris → Lisbon → Paris on a return ticket) DO NOT merge —
    a fresh stop is created for the return arrival per the engine's classifier.

    The same-city-non-sequential rule is the post-Slice-3 engine behaviour:
    a return ticket creates a fresh Paris stop for the return-arrival even
    though the city already exists earlier in the route.
    """

    stops: list[_StopState] = field(default_factory=list)
    transits: list[dict[str, Any]] = field(default_factory=list)
    unattached: list[dict[str, Any]] = field(default_factory=list)

    def _current_stop_for_create(self, city: str, anchor: datetime) -> _StopState:
        """Always create + return a fresh stop for ``city``.

        Used when a transit's arrival lands at a city that "starts a new stop"
        (e.g. a return ticket's final arrival — the engine creates a fresh
        stop even when the city has appeared earlier).
        """
        stop = _StopState(city=city, anchor_at=anchor)
        self.stops.append(stop)
        return stop

    def find_or_create_stop_for_enrich(
        self,
        city: str,
        anchor: datetime,
    ) -> _StopState:
        """Find an existing-and-adjacent stop matching ``city``, else create one.

        "Adjacent" means the most recently appended stop — same-city events
        that fold into the SAME chronological position merge with the latest
        stop. Anything else (a returning leg to a city that appeared earlier
        but is not the latest stop) gets a fresh stop, matching the engine's
        classifier.
        """
        if self.stops and _city_identity(self.stops[-1].city) == _city_identity(city):
            stop = self.stops[-1]
            if stop.anchor_at is None or anchor < stop.anchor_at:
                stop.anchor_at = anchor
            return stop
        return self._current_stop_for_create(city, anchor)


# --------------------------------------------------------------------------- #
# Composer entry point
# --------------------------------------------------------------------------- #


def compose_trip(spec: TripSpec) -> TripBundle:
    """Walk ``spec.primitives`` in order; emit the trip's bundle.

    Composer order: the primitives' order in ``spec.primitives`` is the order
    the manifest carries (PDFs are uploaded in this order; the engine sees
    fragments in this order). The expected route is built by walking ALL
    primitives in this order and applying the per-primitive event logic. Same
    chronological position + same city → enrich the last stop; otherwise → new
    stop.

    Raises :class:`ComposerError` if a primitive's travelers aren't a subset
    of ``spec.travelers``, or if a transit primitive's stations don't pair
    chronologically.
    """
    trip_travelers = _normalize_travelers(spec.travelers)
    trip_travelers_set = set(trip_travelers)

    accumulator = _RouteAccumulator()
    pdfs: list[PDFEntry] = []
    pdf_count_per_kind: dict[str, int] = {}

    for index, primitive in enumerate(spec.primitives, start=1):
        # Manifest entries are numbered by 1-based position within the trip.
        index_slug = f"{index:02d}"
        # The "docname" for the PDF filename — derived from the primitive kind.
        if isinstance(primitive, p.TransitPrimitive):
            docname = _transit_docname(primitive, pdf_count_per_kind)
        elif isinstance(primitive, p.AccommodationPrimitive):
            docname = _accommodation_docname(primitive, pdf_count_per_kind)
        elif isinstance(primitive, p.SupplementaryPrimitive):
            docname = _supplementary_docname(primitive, pdf_count_per_kind)
        else:
            docname = primitive.placeholder_name
        relpath = f"{spec.slug}/{index_slug}-{docname}.pdf"
        scenario_id = f"{spec.slug}/{index_slug}-{docname}"

        if not isinstance(primitive, p.UnreadablePrimitive):
            for traveler in primitive.travelers:
                if traveler not in trip_travelers_set:
                    msg = (
                        f"primitive #{index} ({type(primitive).__name__}) names "
                        f"traveler {traveler!r} not in TripSpec.travelers "
                        f"{trip_travelers!r}"
                    )
                    raise ComposerError(msg)

        # The integration runner uses the manifest path (``layer2/<slug>/...``)
        # as the ``source_document_id`` it hands to the adapter, which then
        # threads it onto every emitted transit's ``sourceFragmentId``. Mirror
        # that here so the expected route's transits match the engine's.
        manifest_pdf_path = f"layer2/{relpath}"

        if isinstance(primitive, p.TransitPrimitive):
            fields = _build_transit_expected_fields(primitive, scenario_id=scenario_id)
            context = _build_transit_render_context(primitive, fields)
            pdfs.append(
                PDFEntry(
                    relpath=relpath,
                    render_template=_TEMPLATE_BY_TRANSIT_MODE[primitive.mode],
                    render_context=context,
                    expected_fields=fields,
                    rendering=primitive.rendering,
                )
            )
            _accumulate_transit(
                accumulator, primitive, source_document_id=manifest_pdf_path
            )

        elif isinstance(primitive, p.AccommodationPrimitive):
            fields = _build_accommodation_expected_fields(primitive, scenario_id=scenario_id)
            context = _build_accommodation_render_context(primitive, fields)
            pdfs.append(
                PDFEntry(
                    relpath=relpath,
                    render_template=_TEMPLATE_BY_ACCOMMODATION_KIND[primitive.kind],
                    render_context=context,
                    expected_fields=fields,
                    rendering=primitive.rendering,
                )
            )
            _accumulate_accommodation(accumulator, primitive)

        elif isinstance(primitive, p.SupplementaryPrimitive):
            fields = _build_supplementary_expected_fields(primitive, scenario_id=scenario_id)
            context = _build_supplementary_render_context(primitive, fields)
            pdfs.append(
                PDFEntry(
                    relpath=relpath,
                    render_template=_SUPPLEMENTARY_TEMPLATE,
                    render_context=context,
                    expected_fields=fields,
                    rendering=primitive.rendering,
                )
            )
            _accumulate_supplementary(
                accumulator,
                primitive,
                source_document_id=manifest_pdf_path,
            )

        else:  # UnreadablePrimitive
            pdfs.append(
                PDFEntry(
                    relpath=relpath,
                    render_template=None,
                    render_context=None,
                    expected_fields=None,
                    rendering="text",
                    expect_unreadable=True,
                )
            )

    expected_route = _finalize_expected_route(
        accumulator,
        trip_travelers=trip_travelers,
    )

    manifest = _build_manifest(spec, pdfs)

    return TripBundle(
        slug=spec.slug,
        manifest=manifest,
        expected_route=expected_route,
        pdfs=pdfs,
    )


# --------------------------------------------------------------------------- #
# Per-primitive accumulator updates
# --------------------------------------------------------------------------- #


def _accumulate_transit(
    acc: _RouteAccumulator,
    primitive: p.TransitPrimitive,
    *,
    source_document_id: str,
) -> None:
    """Append legs and stop merges from one transit primitive."""
    legs = _legs_from_transit(primitive, source_document_id=source_document_id)
    for leg in legs:
        from_stop = acc.find_or_create_stop_for_enrich(leg.from_city, leg.departure_at)
        # The from-stop's departure_at is the LATEST departure observed if
        # multiple primitives set it (chronological merge). For our trips the
        # composer walks chronologically, so a None → set / set → max(prev, new)
        # rule is sufficient and matches what the engine emits.
        if from_stop.departure_at is None or leg.departure_at > from_stop.departure_at:
            from_stop.departure_at = leg.departure_at
        for traveler in leg.travelers:
            from_stop.merge_traveler(traveler)
        if from_stop.anchor_at is None:
            from_stop.anchor_at = leg.departure_at

        # For the to-stop: if the most-recently-appended stop is the same city
        # as the to-city, merge (e.g. a hotel pinned at the destination that
        # came in via a previous primitive); otherwise create a fresh stop —
        # this covers the return-ticket case (Paris → Lisbon → Paris) where
        # the return Paris is a NEW stop because the latest stop is Lisbon.
        if acc.stops and _city_identity(acc.stops[-1].city) == _city_identity(leg.to_city):
            to_stop = acc.stops[-1]
        else:
            to_stop = acc._current_stop_for_create(leg.to_city, leg.arrival_at)
        if to_stop.arrival_at is None or leg.arrival_at < to_stop.arrival_at:
            to_stop.arrival_at = leg.arrival_at
        for traveler in leg.travelers:
            to_stop.merge_traveler(traveler)

        acc.transits.append(
            {
                "from": leg.from_city,
                "to": leg.to_city,
                "mode": leg.mode,
                "departureAt": _iso_z(leg.departure_at),
                "arrivalAt": _iso_z(leg.arrival_at),
                "travelers": list(leg.travelers),
                "sourceFragmentId": leg.source_document_id,
            }
        )


def _accumulate_accommodation(
    acc: _RouteAccumulator,
    primitive: p.AccommodationPrimitive,
) -> None:
    """Pin a hotel/airbnb to its city's stop, applying the engine's split rule.

    The engine's classifier (``rules.classify_event`` +
    ``_sanity_check_would_invert``) forces CREATE rather than ENRICH when an
    accommodation check-in lies strictly AFTER the city stop's known
    arrival/departure window. The most common case: outbound transit arrives
    at city X at 10:45; the hotel checks in at 15:00 the same day. The engine
    treats this as a new stop (15:00 > 10:45), then a follow-up return transit
    enriches the hotel-stop with its departure.

    The composer mirrors this rule so the expected-route matches what the
    engine builds. The hotel-only stop carries no arrival_at / departure_at
    initially; a later transit at the same city CAN enrich it via
    :func:`_accumulate_transit`'s same-last-stop merge.
    """
    last_stop = acc.stops[-1] if acc.stops else None
    same_city = (
        last_stop is not None
        and _city_identity(last_stop.city) == _city_identity(primitive.city)
    )
    needs_split = False
    if same_city and last_stop is not None:
        # Match the engine's accommodation sanity check.
        # If the stop has a known arrival/departure window and the new
        # check-in is strictly after the latest known time at this stop,
        # split into a new stop.
        known_times = [
            t for t in (last_stop.arrival_at, last_stop.departure_at) if t is not None
        ]
        if known_times and primitive.check_in_at > max(known_times):
            needs_split = True

    if same_city and not needs_split and last_stop is not None:
        stop = last_stop
    else:
        stop = acc._current_stop_for_create(primitive.city, primitive.check_in_at)

    for traveler in primitive.travelers:
        stop.merge_traveler(traveler)
    stop.accommodations.append(
        {
            "checkInAt": _iso_z(primitive.check_in_at),
            "checkOutAt": _iso_z(primitive.check_out_at),
            "kind": primitive.kind,
            "identifier": primitive.identifier,
        }
    )


def _accumulate_supplementary(
    acc: _RouteAccumulator,
    primitive: p.SupplementaryPrimitive,
    *,
    source_document_id: str,
) -> None:
    """A supplementary with a venue pins to that stop; without, goes unattached."""
    if primitive.venue_kind is None:
        acc.unattached.append(
            {
                "sourceDocumentId": source_document_id,
                "documentType": "supplementary",
                "prices": [{"amount": round(primitive.price_eur, 2), "currency": "EUR"}],
                "qrCodes": [f"{primitive.qr_prefix}-{source_document_id}"],
            }
        )
        return

    assert primitive.venue_city is not None  # noqa: S101
    assert primitive.venue_identifier is not None  # noqa: S101
    # Use the venue's valid-from anchor; fall back to valid-to; fall back to
    # +inf if neither — the engine attaches venues with no anchor at projection
    # time, which means they pin to the stop without disturbing its order. Our
    # composer reproduces this by attaching to the latest matching stop, OR
    # creating one if none exists, with anchor = datetime.max.
    anchor = primitive.valid_from_at or primitive.valid_to_at or datetime.max.replace(tzinfo=UTC)
    stop = acc.find_or_create_stop_for_enrich(primitive.venue_city, anchor)
    for traveler in primitive.travelers:
        stop.merge_traveler(traveler)
    venue_entry: dict[str, Any] = {
        "kind": primitive.venue_kind,
        "identifier": primitive.venue_identifier,
    }
    if primitive.valid_from_at is not None:
        venue_entry["validFromAt"] = _iso_z(primitive.valid_from_at)
    if primitive.valid_to_at is not None:
        venue_entry["validToAt"] = _iso_z(primitive.valid_to_at)
    stop.venues.append(venue_entry)


# --------------------------------------------------------------------------- #
# Finalisation
# --------------------------------------------------------------------------- #


def _finalize_expected_route(
    acc: _RouteAccumulator,
    *,
    trip_travelers: list[str],
) -> dict[str, Any]:
    """Project the accumulator state into an ``expected-route.json`` dict."""
    stops_payload: list[dict[str, Any]] = []
    for stop in acc.stops:
        if not stop.travelers:
            # Defensive — a stop should always have at least one traveler;
            # composer bugs that land here surface as a clear failure.
            msg = f"composer produced stop {stop.city!r} with no travelers"
            raise ComposerError(msg)
        entry: dict[str, Any] = {
            "city": stop.city,
            "travelers": list(stop.travelers),
        }
        if stop.arrival_at is not None:
            entry["arrivalAt"] = _iso_z(stop.arrival_at)
        if stop.departure_at is not None:
            entry["departureAt"] = _iso_z(stop.departure_at)
        # `scoring.final_route_match` ignores stops[].venues — we emit them
        # anyway so the expected-route is a full description of the engine's
        # eventual route.
        if stop.accommodations:
            entry["accommodations"] = list(stop.accommodations)
        if stop.venues:
            entry["venues"] = list(stop.venues)
        stops_payload.append(entry)

    payload: dict[str, Any] = {
        "travelers": list(trip_travelers),
        "stops": stops_payload,
        "transits": list(acc.transits),
    }
    if acc.unattached:
        payload["unattachedDocuments"] = list(acc.unattached)
    return payload


def _build_manifest(spec: TripSpec, pdfs: list[PDFEntry]) -> dict[str, Any]:
    """Build the trip's ``manifest.json`` payload."""
    documents: list[dict[str, Any]] = []
    for entry in pdfs:
        doc: dict[str, Any] = {"pdf": f"layer2/{entry.relpath}"}
        if entry.expect_unreadable:
            doc["expect_unreadable"] = True
        documents.append(doc)
    manifest: dict[str, Any] = {
        "travelers": _normalize_travelers(spec.travelers),
        "documents": documents,
    }
    if spec.notes is not None:
        manifest["notes"] = spec.notes
    return manifest


# --------------------------------------------------------------------------- #
# Docname helpers
# --------------------------------------------------------------------------- #


def _transit_docname(primitive: p.TransitPrimitive, counts: dict[str, int]) -> str:
    """Mint a stable docname stem for a transit PDF."""
    if len(primitive.stations) >= 3:
        shape = "return"
    else:
        shape = "leg"
    key = f"{primitive.mode}-{shape}"
    counts[key] = counts.get(key, 0) + 1
    return f"{primitive.mode}-{shape}-{counts[key]}"


def _accommodation_docname(primitive: p.AccommodationPrimitive, counts: dict[str, int]) -> str:
    """Mint a stable docname stem for an accommodation PDF."""
    key = primitive.kind
    counts[key] = counts.get(key, 0) + 1
    return f"{primitive.kind}-{counts[key]}"


def _supplementary_docname(primitive: p.SupplementaryPrimitive, counts: dict[str, int]) -> str:
    """Mint a stable docname stem for a supplementary PDF."""
    if primitive.venue_kind is None:
        kind_slug = "supp-nolocation"
    else:
        kind_slug = f"supp-{primitive.venue_kind}"
    counts[kind_slug] = counts.get(kind_slug, 0) + 1
    return f"{kind_slug}-{counts[kind_slug]}"
