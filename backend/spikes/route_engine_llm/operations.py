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
    Transit,
    TransitMode,
    WorkingRoute,
)

__all__ = [
    "OpApplyError",
    "CreateStop",
    "EnrichStop",
    "AddTransit",
    "AttachAccommodation",
    "AddTravelers",
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
    """

    model_config = ConfigDict(extra="forbid")

    op: Literal["create_stop"] = "create_stop"
    city: str = Field(pattern=r"^[A-Z]{3}$")
    after: str | None = None
    ref: str | None = None


class EnrichStop(BaseModel):
    """Fill timing on a referenced stop (existing id or same-batch ref)."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["enrich_stop"] = "enrich_stop"
    stop_id: str = Field(alias="stopId")
    arrival_at: datetime | None = Field(default=None, alias="arrivalAt")
    departure_at: datetime | None = Field(default=None, alias="departureAt")


class AddTransit(BaseModel):
    """Add a transit between two referenced stops (existing ids or same-batch refs)."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["add_transit"] = "add_transit"
    from_stop_id: str = Field(alias="fromStopId")
    to_stop_id: str = Field(alias="toStopId")
    mode: TransitMode
    departure_at: datetime = Field(alias="departureAt")
    arrival_at: datetime = Field(alias="arrivalAt")
    travelers: list[str] = Field(default_factory=list)
    source_fragment_id: str = Field(alias="sourceFragmentId")


class AttachAccommodation(BaseModel):
    """Attach a hotel stay to a referenced stop (existing id or same-batch ref)."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["attach_accommodation"] = "attach_accommodation"
    stop_id: str = Field(alias="stopId")
    check_in_at: datetime = Field(alias="checkInAt")
    check_out_at: datetime = Field(alias="checkOutAt")
    hotel_name: str | None = Field(default=None, alias="hotelName")


class AddTravelers(BaseModel):
    """Union travelers onto a referenced stop (existing id or same-batch ref)."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["add_travelers"] = "add_travelers"
    stop_id: str = Field(alias="stopId")
    travelers: list[str]


type Op = Annotated[
    CreateStop | EnrichStop | AddTransit | AttachAccommodation | AddTravelers,
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
            hotelName=op.hotel_name,
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
