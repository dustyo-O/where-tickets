"""Pure decision functions for the algorithmic engine — no I/O.

``build_ops(route, fragment)`` translates a single fragment into an ordered op
list ready for :func:`spikes.route_engine_llm.operations.apply`.

Slice 2 scope: any transit-ticket fragment (single- or multi-leg) folded into
an empty OR non-empty route, with chronological positioning of newly-created
stops against existing ones. Hotel-booking fragments remain out of scope and
raise :class:`RuleNotImplementedError` (Slice 4). The per-traveler-per-slot
identity classifier lands in Slice 3 and refines the simple "reuse the existing
same-city stop" rule used here.
"""

# NOTE: imported from the LLM spike's package because the shared types
# (models / operations / corpus / scoring / report) currently live there.
# TODO: extract to a common `engine_core` package when one engine is
# promoted to production (per 003-algorithmic-engine-spike §2.1).
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from spikes.route_engine_llm.models import (
    Fragment,
    HotelBookingFragment,
    TransitTicketFragment,
    WorkingRoute,
)
from spikes.route_engine_llm.operations import AddTransit, CreateStop, Op

if TYPE_CHECKING:  # pragma: no cover - typing only
    from spikes.route_engine_llm.models import RouteStop

__all__ = ["RuleNotImplementedError", "build_ops", "find_after_neighbor"]


class RuleNotImplementedError(Exception):
    """A fragment shape the current slice does not yet handle.

    Slice 2 covers transit tickets (any number of legs) folded into empty or
    non-empty routes. Hotel-booking fragments still raise this — the engine
    wraps it as an :class:`EngineError` so the scenario buckets cleanly.
    """


def build_ops(route: WorkingRoute, fragment: Fragment) -> list[Op]:
    """Translate ``fragment`` into the ordered op list for ``route``.

    Slice 2 scope: transit-ticket fragments only (any leg count), against an
    empty or non-empty route. Hotel-booking fragments raise
    :class:`RuleNotImplementedError`.
    """
    if isinstance(fragment, HotelBookingFragment):
        msg = "slice-2 rules do not handle hotel-booking fragments yet (Slice 4)"
        raise RuleNotImplementedError(msg)

    if not isinstance(fragment, TransitTicketFragment):  # pragma: no cover - defensive
        msg = f"slice-2 rules do not handle fragment type: {type(fragment).__name__}"
        raise RuleNotImplementedError(msg)

    if not fragment.legs:  # pragma: no cover - schema forbids empty legs
        msg = "transit ticket has no legs"
        raise RuleNotImplementedError(msg)

    mode = _ticket_mode(fragment.document_type)
    travelers = list(fragment.travelers)
    source_id = fragment.source_document_id

    ops: list[Op] = []

    # Per-leg-endpoint resolver: returns either an existing `stop-N` id, a
    # batch-local `ref` already declared in this batch, or mints a fresh
    # `CreateStop` op (returning its ref). The endpoint's time guides the
    # chronological-insertion `after`-neighbor pick when we have to create.
    next_ref_index = 1
    # Track refs we created this batch and the time we anchored them at, so
    # subsequent in-batch creations can chain off the previous ref via `after`.
    last_batch_ref: str | None = None

    # `endpoint_handles[i]` is the token (existing id or batch ref) to use for
    # the city *arriving* at the end of leg i (equivalently the city departing
    # from leg i+1). We compute handle-for-from and handle-for-to as we walk.
    def _handle_for_city(city: str, *, anchor_time: datetime) -> str:
        """Return a token (existing id or batch ref) that resolves to ``city``.

        Precedence:
          1. If a previous leg in this same batch already minted a stop for
             ``city``, reuse that batch ref so the applier doesn't duplicate.
          2. If a same-city stop already exists in the route, reuse its id.
             (Slice 3's per-traveler-per-slot classifier refines this for
             revisits/loops; Slice 2 picks any same-city stop.)
          3. Otherwise mint a fresh ``CreateStop`` op with a new ref and an
             ``after`` chosen chronologically against existing stops, chained
             off the previous in-batch ref when present.
        """
        nonlocal next_ref_index, last_batch_ref

        # (1) Reuse an in-batch ref for the same city.
        for op in ops:
            if isinstance(op, CreateStop) and op.ref is not None and op.city == city:
                return op.ref

        # (2) Reuse an existing same-city stop. Slice 2 picks the first one;
        # Slice 3 replaces this with the per-traveler-per-slot classifier.
        for stop in route.stops:
            if stop.city == city:
                return stop.id

        # (3) Mint a new stop.
        ref = f"n{next_ref_index}"
        next_ref_index += 1
        if last_batch_ref is not None:
            # Chain after the previously-created in-batch stop. The applier
            # resolves the ref to the freshly-minted stop-N id at apply time.
            after: str | None = last_batch_ref
        else:
            after = find_after_neighbor(route, anchor_time)
        ops.append(CreateStop(city=city, after=after, ref=ref))
        last_batch_ref = ref
        return ref

    # Walk the legs in fragment order, emitting one AddTransit per leg.
    for leg in fragment.legs:
        from_handle = _handle_for_city(leg.from_, anchor_time=leg.departure_at)
        to_handle = _handle_for_city(leg.to, anchor_time=leg.arrival_at)
        ops.append(
            AddTransit.model_validate(
                {
                    "fromStopId": from_handle,
                    "toStopId": to_handle,
                    "mode": mode,
                    "departureAt": leg.departure_at,
                    "arrivalAt": leg.arrival_at,
                    "travelers": list(travelers),
                    "sourceFragmentId": source_id,
                }
            )
        )

    return ops


def find_after_neighbor(route: WorkingRoute, new_stop_time: datetime) -> str | None:
    """Pick the existing-stop ``after`` neighbor for a new stop at ``new_stop_time``.

    Returns the id of the latest existing stop whose projected time is
    ``<= new_stop_time``, or ``None`` (i.e. prepend) if the new stop precedes
    every existing stop. An empty route also returns ``None``.

    "Projected time" prefers a stop's ``arrival_at`` (when it has incoming
    transits) and falls back to ``departure_at`` (origin stops). Stops with
    neither time set are treated as preceding everything — they have no
    chronological signal so they fall to the back of the comparison ordering.
    """
    if not route.stops:
        return None

    # Sort existing stops by projected time. Use a large sentinel for stops
    # with no projected time so they sort to the end — they shouldn't dictate
    # placement for a time-bearing new stop.
    timed: list[tuple[datetime, RouteStop]] = []
    for stop in route.stops:
        proj = _projected_time(stop)
        if proj is not None:
            timed.append((proj, stop))

    if not timed:
        # No timed stops to compare against — fall back to appending after the
        # last stop in route order so we don't accidentally reorder them.
        return route.stops[-1].id

    timed.sort(key=lambda pair: pair[0])

    # Walk back-to-front for the latest stop with time <= new_stop_time.
    best_id: str | None = None
    for proj, stop in timed:
        if proj <= new_stop_time:
            best_id = stop.id
        else:
            break
    return best_id


def _projected_time(stop: RouteStop) -> datetime | None:
    """Return the stop's chronological position: arrival first, else departure."""
    if stop.arrival_at is not None:
        return stop.arrival_at
    return stop.departure_at


def _ticket_mode(document_type: str) -> str:
    """Map a transit-ticket ``documentType`` to its ``TransitMode`` value."""
    match document_type:
        case "air-ticket":
            return "air"
        case "bus-ticket":
            return "bus"
        case "train-ticket":
            return "train"
        case _:  # pragma: no cover - Fragment union forbids other values
            msg = f"unknown transit ticket document type: {document_type!r}"
            raise RuleNotImplementedError(msg)
