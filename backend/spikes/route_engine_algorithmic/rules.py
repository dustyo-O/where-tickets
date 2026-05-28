"""Pure decision functions for the algorithmic engine — no I/O.

``build_ops(route, fragment)`` translates a single fragment into an ordered op
list ready for :func:`spikes.route_engine_llm.operations.apply`. Slice 1 scope
is intentionally narrow: ONE transit-ticket fragment with exactly ONE leg,
applied to an EMPTY :class:`WorkingRoute`. Every other shape (multi-leg ticket,
hotel booking, non-empty route) raises :class:`RuleNotImplementedError` — the
engine wraps that into an :class:`EngineError` so the scenario buckets cleanly
without aborting the sweep. Later slices flesh out the per-traveler-per-slot
identity classifier on top of this skeleton.
"""

# NOTE: imported from the LLM spike's package because the shared types
# (models / operations / corpus / scoring / report) currently live there.
# TODO: extract to a common `engine_core` package when one engine is
# promoted to production (per 003-algorithmic-engine-spike §2.1).
from __future__ import annotations

from spikes.route_engine_llm.models import (
    Fragment,
    HotelBookingFragment,
    TransitTicketFragment,
    WorkingRoute,
)
from spikes.route_engine_llm.operations import AddTransit, CreateStop, Op

__all__ = ["RuleNotImplementedError", "build_ops"]


class RuleNotImplementedError(Exception):
    """A fragment shape the current slice does not yet handle.

    Slice 1 only covers single-leg transit tickets on an empty route. Anything
    else (multi-leg ticket, hotel booking, non-empty route) raises this — the
    engine wraps it as an :class:`EngineError` so the scenario fails cleanly.
    """


def build_ops(route: WorkingRoute, fragment: Fragment) -> list[Op]:
    """Translate ``fragment`` into the ordered op list for ``route``.

    Slice 1 scope: single-leg transit ticket on an empty route. Any other shape
    raises :class:`RuleNotImplementedError`.
    """
    if route.stops or route.transits:
        msg = (
            "slice-1 rules only handle an empty route; got route with "
            f"{len(route.stops)} stops and {len(route.transits)} transits"
        )
        raise RuleNotImplementedError(msg)

    if isinstance(fragment, HotelBookingFragment):
        msg = "slice-1 rules do not handle hotel-booking fragments yet"
        raise RuleNotImplementedError(msg)

    if not isinstance(fragment, TransitTicketFragment):  # pragma: no cover - defensive
        msg = f"slice-1 rules do not handle fragment type: {type(fragment).__name__}"
        raise RuleNotImplementedError(msg)

    if len(fragment.legs) != 1:
        msg = (
            "slice-1 rules only handle single-leg transit tickets; got "
            f"{len(fragment.legs)} legs"
        )
        raise RuleNotImplementedError(msg)

    leg = fragment.legs[0]
    mode = _ticket_mode(fragment.document_type)

    return [
        CreateStop(city=leg.from_, after=None, ref="n1"),
        CreateStop(city=leg.to, after="n1", ref="n2"),
        AddTransit.model_validate(
            {
                "fromStopId": "n1",
                "toStopId": "n2",
                "mode": mode,
                "departureAt": leg.departure_at,
                "arrivalAt": leg.arrival_at,
                "travelers": list(fragment.travelers),
                "sourceFragmentId": fragment.source_document_id,
            }
        ),
    ]


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
