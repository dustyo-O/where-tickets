"""The LLM operation set + a deterministic applier.

The LLM returns a list of operations that reference engine-owned stop IDs. The
applier validates every referenced ID and mutates the route in place. New IDs
are minted only here, monotonically; existing IDs are never reassigned.

Apply semantics:
- `create_stop`   mint a fresh `stop-N`, splice after `after` (or front).
- `enrich_stop`   fill arrival/departure on an existing stop; a *different*
                  existing value is a conflict (raise), the same value is a no-op.
- `add_travelers` union travelers onto a stop, stable order, no duplicates.
- `attach_accommodation` append an Accommodation to a stop.
- `add_transit`   mint a fresh `transit-N` between two existing stops.

Any reference to an unknown ID raises `OpApplyError`.
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

    `after` is an existing stop id to insert after, or None / "start" to
    prepend at the front of the route.
    """

    model_config = ConfigDict(extra="forbid")

    op: Literal["create_stop"] = "create_stop"
    city: str = Field(pattern=r"^[A-Z]{3}$")
    after: str | None = None


class EnrichStop(BaseModel):
    """Fill timing on an existing stop."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["enrich_stop"] = "enrich_stop"
    stop_id: str = Field(alias="stopId")
    arrival_at: datetime | None = Field(default=None, alias="arrivalAt")
    departure_at: datetime | None = Field(default=None, alias="departureAt")


class AddTransit(BaseModel):
    """Add a transit between two existing stops."""

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
    """Attach a hotel stay to an existing stop."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["attach_accommodation"] = "attach_accommodation"
    stop_id: str = Field(alias="stopId")
    check_in_at: datetime = Field(alias="checkInAt")
    check_out_at: datetime = Field(alias="checkOutAt")
    hotel_name: str | None = Field(default=None, alias="hotelName")


class AddTravelers(BaseModel):
    """Union travelers onto an existing stop."""

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

    Existing stop/transit IDs are never reassigned. Raises `OpApplyError` on a
    dangling reference or a conflicting enrichment.
    """
    for op in ops:
        match op:
            case CreateStop():
                _apply_create_stop(route, op)
            case EnrichStop():
                _apply_enrich_stop(route, op)
            case AddTransit():
                _apply_add_transit(route, op)
            case AttachAccommodation():
                _apply_attach_accommodation(route, op)
            case AddTravelers():
                _apply_add_travelers(route, op)
    return route


def _resolve_stop(route: WorkingRoute, stop_id: str) -> RouteStop:
    stop = route.stop_by_id(stop_id)
    if stop is None:
        msg = f"operation references unknown stop id: {stop_id!r}"
        raise OpApplyError(msg)
    return stop


def _apply_create_stop(route: WorkingRoute, op: CreateStop) -> None:
    after_id: str | None = None if op.after in (None, _START) else op.after
    if after_id is not None and not route.has_stop(after_id):
        msg = f"create_stop references unknown `after` stop id: {after_id!r}"
        raise OpApplyError(msg)
    stop = RouteStop(id=route.mint_stop_id(), city=op.city)
    route.insert_stop(stop, after_id)


def _apply_enrich_stop(route: WorkingRoute, op: EnrichStop) -> None:
    stop = _resolve_stop(route, op.stop_id)
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


def _apply_add_transit(route: WorkingRoute, op: AddTransit) -> None:
    _resolve_stop(route, op.from_stop_id)
    _resolve_stop(route, op.to_stop_id)
    route.transits.append(
        Transit(
            id=route.mint_transit_id(),
            fromStopId=op.from_stop_id,
            toStopId=op.to_stop_id,
            mode=op.mode,
            departureAt=op.departure_at,
            arrivalAt=op.arrival_at,
            travelers=list(op.travelers),
            sourceFragmentId=op.source_fragment_id,
        )
    )


def _apply_attach_accommodation(route: WorkingRoute, op: AttachAccommodation) -> None:
    stop = _resolve_stop(route, op.stop_id)
    stop.accommodations.append(
        Accommodation(
            checkInAt=op.check_in_at,
            checkOutAt=op.check_out_at,
            hotelName=op.hotel_name,
        )
    )


def _apply_add_travelers(route: WorkingRoute, op: AddTravelers) -> None:
    stop = _resolve_stop(route, op.stop_id)
    existing = set(stop.travelers)
    for traveler in op.travelers:
        if traveler not in existing:
            stop.travelers.append(traveler)
            existing.add(traveler)
