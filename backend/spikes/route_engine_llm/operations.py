"""The LLM operation set + a deterministic applier.

The LLM returns a list of operations that reference stops. The applier validates
every referenced stop and mutates the route in place. New IDs are minted only
here, monotonically; existing IDs are never reassigned.

Stop references — existing ids vs. batch-local refs:
- An EXISTING stop is referenced by its engine-owned id (`stop-N`).
- A stop CREATED WITHIN THE SAME batch is referenced by a model-chosen `ref`
  (a temp handle declared on the creating `create_stop`, e.g. `"n1"`). Refs are
  batch-local: the applier resolves each ref to the minted `stop-N` id and never
  persists the ref. The `WorkingRoute` only ever stores real `stop-N` ids, so
  identity preservation and scoring are unaffected.

Resolution precedence for any stop reference token (`_resolve_stop`):
  (a) if the token is a `ref` declared earlier in this batch -> its minted id;
  (b) elif it's an existing stop id in the route -> that stop;
  (c) else -> `OpApplyError` (dangling / undeclared / forward ref).

Apply semantics:
- `create_stop`   mint a fresh `stop-N`, splice after `after` (an existing id, a
                  same-batch ref, or None/"start" to prepend). If the op carries
                  a `ref`, record `ref -> minted id` for later ops in this batch.
- `enrich_stop`   fill arrival/departure on a referenced stop; a *different*
                  existing value is a conflict (raise), the same value is a no-op.
- `add_travelers` union travelers onto a stop, stable order, no duplicates.
- `attach_accommodation` append an Accommodation to a stop.
- `add_transit`   mint a fresh `transit-N` between two referenced stops.

A reference to an unknown id, an undeclared/forward ref, or a duplicate `ref`
declaration raises `OpApplyError`.

Stop projection (runs at the END of every `apply` call):
The display fields stops carry in the expected route — `arrivalAt`,
`departureAt`, and `travelers` — naturally live on the transits the model wires
up, not on the stops. So after every batch the applier DERIVES those stop fields
from the incident transits, with a hybrid precedence:

- `arrivalAt`  fill-only: an explicit value already on the stop (from an
               `enrich_stop`) wins; otherwise it is taken from the transit(s)
               ending at the stop (latest `arrivalAt` if several); else `None`.
- `departureAt` fill-only: symmetric — explicit wins; otherwise from the
               transit(s) leaving the stop (earliest `departureAt` if several);
               else `None`.
- `travelers`  union (additive, never clobbered): any travelers already on the
               stop (from `add_travelers`) plus the travelers of EVERY transit
               incident to the stop (incoming or outgoing), stable order, no
               duplicates.

Projection only fills derived display fields on EXISTING stops; it never adds,
removes, or reorders stops and never touches ids — so identity/ordering checks
are unaffected. `enrich_stop`/`add_travelers` stay meaningful as overrides for a
stop with no transit (e.g. accommodation-only city).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from spikes.route_engine_llm.models import (
    Accommodation,
    RouteStop,
    Station,
    Transit,
    TransitMode,
    UnattachedDocument,
    Venue,
    WorkingRoute,
)

__all__ = [
    "OpApplyError",
    "CreateStop",
    "EnrichStop",
    "AddTransit",
    "AttachAccommodation",
    "AddTravelers",
    "AddStations",
    "AttachVenue",
    "AddUnattachedDocument",
    "Op",
    "apply",
]

# Sentinel `after` value meaning "prepend at the front of the route".
_START = "start"


class OpApplyError(Exception):
    """Raised when an operation references an unknown ID or conflicts with state."""


# --------------------------------------------------------------------------- #
# Operation models (discriminated union on `op`)
# --------------------------------------------------------------------------- #


class CreateStop(BaseModel):
    """Create a new stop with a fresh engine ID.

    `after` is a stop reference to insert after (an existing `stop-N` id OR a
    same-batch `ref`), or None / "start" to prepend at the front of the route.

    `ref` is an optional model-chosen temp handle for THIS new stop (e.g. "n1"),
    unique within the batch. Later ops in the same batch may reference this stop
    by that `ref` exactly as they would reference an existing stop by its id.
    The handle is batch-local and never persisted.

    `stations` is an optional set of station entries to seed onto the new stop
    (typically one entry for transit ticket events — the station the rules
    classified as either the leg origin or destination). Duplicates by
    ``(city, kind, identifier)`` are suppressed at apply time.
    """

    model_config = ConfigDict(extra="forbid")

    op: Literal["create_stop"] = "create_stop"
    city: str
    after: str | None = None
    ref: str | None = None
    stations: list[Station] = Field(default_factory=list)


class EnrichStop(BaseModel):
    """Fill timing on a referenced stop (existing id or same-batch ref)."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["enrich_stop"] = "enrich_stop"
    stop_id: str = Field(alias="stopId")
    arrival_at: datetime | None = Field(default=None, alias="arrivalAt")
    departure_at: datetime | None = Field(default=None, alias="departureAt")


class AddTransit(BaseModel):
    """Add a transit between two referenced stops (existing ids or same-batch refs)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    op: Literal["add_transit"] = "add_transit"
    from_stop_id: str = Field(alias="fromStopId")
    to_stop_id: str = Field(alias="toStopId")
    mode: TransitMode
    departure_at: datetime = Field(alias="departureAt")
    arrival_at: datetime = Field(alias="arrivalAt")
    travelers: list[str] = Field(default_factory=list)
    source_fragment_id: str = Field(alias="sourceFragmentId")
    # Which station within the from/to city the transit actually leaves /
    # arrives at; copied onto the minted Transit. Optional so callers that
    # don't yet carry station detail (e.g. LLM spike fixtures) keep working.
    origin_station: Station | None = Field(default=None, alias="originStation")
    destination_station: Station | None = Field(
        default=None, alias="destinationStation"
    )


class AttachAccommodation(BaseModel):
    """Attach an accommodation stay to a referenced stop.

    DUS-31 Slice 5: ``kind`` widens to ``{"hotel", "airbnb"}``; airbnb rides
    the existing accommodation path.
    """

    model_config = ConfigDict(extra="forbid")

    op: Literal["attach_accommodation"] = "attach_accommodation"
    stop_id: str = Field(alias="stopId")
    check_in_at: datetime = Field(alias="checkInAt")
    check_out_at: datetime = Field(alias="checkOutAt")
    kind: Literal["hotel", "airbnb"]
    identifier: str


class AddTravelers(BaseModel):
    """Union travelers onto a referenced stop (existing id or same-batch ref)."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["add_travelers"] = "add_travelers"
    stop_id: str = Field(alias="stopId")
    travelers: list[str]


class AddStations(BaseModel):
    """Union stations onto a referenced stop (existing id or same-batch ref).

    Mirrors :class:`AddTravelers`'s shape — ``target`` is a stop reference (an
    existing ``stop-N`` id OR a same-batch ``ref``), and ``stations`` is the
    list to append. The applier de-duplicates against existing entries on the
    target stop by ``(city, kind, identifier)`` so two same-fragment legs
    touching the same station never double-attach it.
    """

    model_config = ConfigDict(extra="forbid")

    op: Literal["add_stations"] = "add_stations"
    target: str
    stations: list[Station]


class AttachVenue(BaseModel):
    """Attach a venue to a referenced stop (existing id or same-batch ref).

    DUS-31 Slice 5. Mirrors :class:`AttachAccommodation`'s shape on the
    venue side. ``target`` is a stop reference; ``venue`` is the
    :class:`Venue` to append. The applier de-duplicates against existing
    entries on the stop by ``(kind, identifier)`` — venues are
    stop-attached regardless of traveler, so the dedupe key does NOT include
    travelers.
    """

    model_config = ConfigDict(extra="forbid")

    op: Literal["attach_venue"] = "attach_venue"
    target: str
    venue: Venue


class AddUnattachedDocument(BaseModel):
    """Append an unattached supplementary document to the working route.

    DUS-31 Slice 5. Intentionally identity-clean: the applier does NOT touch
    ``stops[]``, ``transits[]``, ``next_stop_seq``, or ``next_transit_seq`` —
    it just appends ``document`` to ``WorkingRoute.unattached_documents``.
    The unattached list is strictly invisible to ``scoring.final_route_match``
    / ``identity_preserved`` / ``ordering_consistent`` so this op cannot
    affect any of the engine's scoring gates.
    """

    model_config = ConfigDict(extra="forbid")

    op: Literal["add_unattached_document"] = "add_unattached_document"
    document: UnattachedDocument


type Op = Annotated[
    CreateStop
    | EnrichStop
    | AddTransit
    | AttachAccommodation
    | AddTravelers
    | AddStations
    | AttachVenue
    | AddUnattachedDocument,
    Field(discriminator="op"),
]


# --------------------------------------------------------------------------- #
# Applier
# --------------------------------------------------------------------------- #


def apply(route: WorkingRoute, ops: list[Op]) -> WorkingRoute:
    """Apply `ops` to `route` in order, mutating and returning the same route.

    Existing stop/transit IDs are never reassigned. A batch-local `ref -> minted
    id` map lets ops reference stops created earlier in this same batch. Raises
    `OpApplyError` on a dangling/undeclared/forward reference, a duplicate `ref`
    declaration, or a conflicting enrichment.
    """
    # Batch-local refs: model-chosen handle -> the `stop-N` id minted for it.
    refs: dict[str, str] = {}
    for op in ops:
        match op:
            case CreateStop():
                _apply_create_stop(route, op, refs)
            case EnrichStop():
                _apply_enrich_stop(route, op, refs)
            case AddTransit():
                _apply_add_transit(route, op, refs)
            case AttachAccommodation():
                _apply_attach_accommodation(route, op, refs)
            case AddTravelers():
                _apply_add_travelers(route, op, refs)
            case AddStations():
                _apply_add_stations(route, op, refs)
            case AttachVenue():
                _apply_attach_venue(route, op, refs)
            case AddUnattachedDocument():
                _apply_add_unattached_document(route, op)
    _project_stops(route)
    return route


def _project_stops(route: WorkingRoute) -> None:
    """Derive each stop's `arrivalAt`/`departureAt`/`travelers` from transits.

    Hybrid precedence (see module docstring): timing is fill-only (explicit
    `enrich_stop` values win, transits fill the rest), travelers are a union of
    any explicit `add_travelers` values plus every incident transit's travelers.

    Identity-safe: only display fields on existing stops change; stops, their
    order, and their ids are untouched.
    """
    for stop in route.stops:
        incoming = [t for t in route.transits if t.to_stop_id == stop.id]
        outgoing = [t for t in route.transits if t.from_stop_id == stop.id]

        if stop.arrival_at is None and incoming:
            stop.arrival_at = max(t.arrival_at for t in incoming)
        if stop.departure_at is None and outgoing:
            stop.departure_at = min(t.departure_at for t in outgoing)

        # Union: keep existing (explicit) travelers, then add every incident
        # transit's travelers, stable order, no duplicates.
        seen = set(stop.travelers)
        for transit in incoming + outgoing:
            for traveler in transit.travelers:
                if traveler not in seen:
                    stop.travelers.append(traveler)
                    seen.add(traveler)


def _resolve_stop(route: WorkingRoute, token: str, refs: dict[str, str]) -> RouteStop:
    """Resolve a stop reference token to a route stop.

    Precedence: (a) a `ref` declared earlier in this batch -> its minted id;
    (b) else an existing stop id in the route; (c) else `OpApplyError`.
    """
    resolved_id = refs.get(token, token)
    stop = route.stop_by_id(resolved_id)
    if stop is None:
        msg = f"operation references unknown stop: {token!r}"
        raise OpApplyError(msg)
    return stop


def _apply_create_stop(
    route: WorkingRoute, op: CreateStop, refs: dict[str, str]
) -> None:
    if op.ref is not None and op.ref in refs:
        msg = f"create_stop declares duplicate ref: {op.ref!r}"
        raise OpApplyError(msg)
    # Resolve `after` against refs first, then existing ids; sentinel -> prepend.
    after_id: str | None
    if op.after in (None, _START):
        after_id = None
    else:
        after_id = _resolve_stop(route, op.after, refs).id
    stop = RouteStop(id=route.mint_stop_id(), city=op.city)
    # Seed stations carried by the op (deduped within the seed payload itself
    # by (city, kind, identifier); the union helper below is a no-op for an
    # empty stop but keeps the dedupe rule in one place).
    _extend_stations_uniq(stop.stations, op.stations)
    route.insert_stop(stop, after_id)
    if op.ref is not None:
        refs[op.ref] = stop.id


def _apply_enrich_stop(
    route: WorkingRoute, op: EnrichStop, refs: dict[str, str]
) -> None:
    stop = _resolve_stop(route, op.stop_id, refs)
    if op.arrival_at is not None:
        stop.arrival_at = _set_or_conflict(
            field="arrivalAt",
            stop_id=stop.id,
            current=stop.arrival_at,
            incoming=op.arrival_at,
        )
    if op.departure_at is not None:
        stop.departure_at = _set_or_conflict(
            field="departureAt",
            stop_id=stop.id,
            current=stop.departure_at,
            incoming=op.departure_at,
        )


def _set_or_conflict(
    *,
    field: str,
    stop_id: str,
    current: datetime | None,
    incoming: datetime,
) -> datetime:
    """Return `incoming` if the field is unset or unchanged; raise on conflict."""
    if current is not None and current != incoming:
        msg = (
            f"enrich_stop conflict on {field} for {stop_id!r}: "
            f"existing {current.isoformat()} != incoming {incoming.isoformat()}"
        )
        raise OpApplyError(msg)
    return incoming


def _apply_add_transit(
    route: WorkingRoute, op: AddTransit, refs: dict[str, str]
) -> None:
    # Resolve to real `stop-N` ids — refs are never persisted on the transit.
    from_stop = _resolve_stop(route, op.from_stop_id, refs)
    to_stop = _resolve_stop(route, op.to_stop_id, refs)
    route.transits.append(
        Transit(
            id=route.mint_transit_id(),
            fromStopId=from_stop.id,
            toStopId=to_stop.id,
            mode=op.mode,
            departureAt=op.departure_at,
            arrivalAt=op.arrival_at,
            travelers=list(op.travelers),
            sourceFragmentId=op.source_fragment_id,
            originStation=op.origin_station,
            destinationStation=op.destination_station,
        )
    )


def _apply_attach_accommodation(
    route: WorkingRoute, op: AttachAccommodation, refs: dict[str, str]
) -> None:
    stop = _resolve_stop(route, op.stop_id, refs)
    stop.accommodations.append(
        Accommodation(
            checkInAt=op.check_in_at,
            checkOutAt=op.check_out_at,
            kind=op.kind,
            identifier=op.identifier,
        )
    )


def _apply_add_travelers(
    route: WorkingRoute, op: AddTravelers, refs: dict[str, str]
) -> None:
    stop = _resolve_stop(route, op.stop_id, refs)
    existing = set(stop.travelers)
    for traveler in op.travelers:
        if traveler not in existing:
            stop.travelers.append(traveler)
            existing.add(traveler)


def _apply_add_stations(
    route: WorkingRoute, op: AddStations, refs: dict[str, str]
) -> None:
    stop = _resolve_stop(route, op.target, refs)
    _extend_stations_uniq(stop.stations, op.stations)


def _apply_attach_venue(
    route: WorkingRoute, op: AttachVenue, refs: dict[str, str]
) -> None:
    """Append ``op.venue`` to the resolved stop, deduped by ``(kind, identifier)``.

    Travelers are intentionally NOT part of the dedupe key — a venue is
    attached to the stop as a whole, not to a per-traveler slot.
    """
    stop = _resolve_stop(route, op.target, refs)
    seen = {(v.kind, v.identifier) for v in stop.venues}
    key = (op.venue.kind, op.venue.identifier)
    if key in seen:
        return
    stop.venues.append(op.venue)


def _apply_add_unattached_document(
    route: WorkingRoute, op: AddUnattachedDocument
) -> None:
    """Append ``op.document`` to ``route.unattached_documents`` — nothing else.

    Identity-clean by design: stops, transits, and the two id counters are
    untouched, so this op cannot affect any of the scoring gates (which
    ignore ``unattached_documents`` entirely).
    """
    route.unattached_documents.append(op.document)


def _station_identity(station: Station) -> tuple[str, str, str]:
    """Comparison key used to suppress duplicates within a stop's stations[]."""
    return (station.city, station.kind, station.identifier)


def _extend_stations_uniq(target: list[Station], incoming: list[Station]) -> None:
    """Append ``incoming`` to ``target`` deduped by (city, kind, identifier).

    Preserves insertion order and skips entries already present on ``target``
    OR repeated within ``incoming`` itself.
    """
    seen: set[tuple[str, str, str]] = {_station_identity(s) for s in target}
    for station in incoming:
        key = _station_identity(station)
        if key in seen:
            continue
        target.append(station)
        seen.add(key)
