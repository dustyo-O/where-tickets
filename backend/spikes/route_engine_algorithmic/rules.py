"""Pure decision functions for the algorithmic engine — no I/O.

``build_ops(route, fragment)`` translates a single fragment into an ordered op
list ready for :func:`spikes.route_engine_llm.operations.apply`.

Slice 3 scope: any transit-ticket fragment (single- or multi-leg) folded into
an empty OR non-empty route, with the per-traveler-per-slot identity
classifier (:func:`classify_event`) replacing the Slice-2 "reuse the first
same-city stop" rule. The classifier is a faithful code translation of the
LLM prompt's three explicit conditions + the arrival-after-departure sanity
check (see ``spikes.route_engine_llm.prompts.SYSTEM_PROMPT``). Hotel-booking
fragments remain out of scope and raise :class:`RuleNotImplementedError`
(Slice 4).

Pending-projection ledger
-------------------------
The classifier consults a per-batch ledger (``dict[token, _Pending]``) that
overlays the real route with the events we have already classified earlier in
the SAME fragment-batch. The ledger tracks per-role contributions (arrival /
departure) per traveler so condition (c) — "this traveler's slot is already
filled at a different time" — sees in-batch additions as well as the real
``route.transits``. The same ledger surfaces an updated projected time for an
existing stop after an in-batch enrichment, so condition (b) sees the
fragment's own timing when deciding the next event.
"""

# NOTE: imported from the LLM spike's package because the shared types
# (models / operations / corpus / scoring / report) currently live there.
# TODO: extract to a common `engine_core` package when one engine is
# promoted to production (per 003-algorithmic-engine-spike §2.1).
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
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

__all__ = [
    "Decision",
    "DecisionKind",
    "Event",
    "EventRole",
    "RuleNotImplementedError",
    "build_ops",
    "classify_event",
    "find_after_neighbor",
]


class RuleNotImplementedError(Exception):
    """A fragment shape the current slice does not yet handle.

    Slice 3 covers transit tickets (any number of legs) folded into empty or
    non-empty routes, with the per-traveler-per-slot identity classifier.
    Hotel-booking fragments still raise this — the engine wraps it as an
    :class:`EngineError` so the scenario buckets cleanly.
    """


class EventRole(StrEnum):
    """The slot a city event fills at its stop."""

    ARRIVAL = "arrival"
    DEPARTURE = "departure"
    ACCOMMODATION = "accommodation"  # reserved for Slice 4 — not used yet.


@dataclass(frozen=True, slots=True)
class Event:
    """One per-city occurrence implied by a fragment leg or accommodation."""

    city: str
    time: datetime
    role: EventRole
    travelers: tuple[str, ...]


class DecisionKind(StrEnum):
    """Whether the event maps to a new physical stop or enriches an existing one."""

    CREATE = "create"
    ENRICH = "enrich"


@dataclass(frozen=True, slots=True)
class Decision:
    """Result of :func:`classify_event` — CREATE or ENRICH(target_stop_id)."""

    kind: DecisionKind
    # For ENRICH: the existing stop id to target. None for CREATE.
    target_stop_id: str | None = None

    @classmethod
    def create(cls) -> Decision:
        return cls(kind=DecisionKind.CREATE)

    @classmethod
    def enrich(cls, target_stop_id: str) -> Decision:
        return cls(kind=DecisionKind.ENRICH, target_stop_id=target_stop_id)


# --------------------------------------------------------------------------- #
# build_ops — fragment -> op list
# --------------------------------------------------------------------------- #


def build_ops(route: WorkingRoute, fragment: Fragment) -> list[Op]:
    """Translate ``fragment`` into the ordered op list for ``route``.

    Slice 3 scope: transit-ticket fragments only. Each leg contributes two
    events (departure from `from_`, arrival at `to`); each event is routed
    through :func:`classify_event` to decide CREATE-new vs ENRICH-existing.
    Hotel-booking fragments raise :class:`RuleNotImplementedError`.
    """
    if isinstance(fragment, HotelBookingFragment):
        msg = "slice-3 rules do not handle hotel-booking fragments yet (Slice 4)"
        raise RuleNotImplementedError(msg)

    if not isinstance(fragment, TransitTicketFragment):  # pragma: no cover - defensive
        msg = f"slice-3 rules do not handle fragment type: {type(fragment).__name__}"
        raise RuleNotImplementedError(msg)

    if not fragment.legs:  # pragma: no cover - schema forbids empty legs
        msg = "transit ticket has no legs"
        raise RuleNotImplementedError(msg)

    mode = _ticket_mode(fragment.document_type)
    travelers = list(fragment.travelers)
    travelers_t = tuple(travelers)
    source_id = fragment.source_document_id

    ops: list[Op] = []
    pending = _PendingLedger.from_route(route)
    # Pre-collect every event the fragment will emit so condition (b) sees
    # later-in-the-batch different-city stops as intervening time anchors
    # when classifying earlier-in-the-batch events. Mirrors the prompt's
    # Example B reasoning, where the fragment's OWN cities count as the
    # intervening different-city stops between an event and an existing
    # same-city stop.
    fragment_events = _fragment_events(fragment, travelers_t)
    next_ref_index = 1
    # Track the last in-batch ref we minted so chained creates `after` it.
    last_batch_ref: str | None = None

    def _resolve_event(event: Event) -> str:
        """Return a handle (existing id or batch ref) for `event`, minting if needed."""
        nonlocal next_ref_index, last_batch_ref

        decision = classify_event(
            route,
            event,
            pending=pending,
            fragment_events=fragment_events,
        )
        if decision.kind is DecisionKind.ENRICH:
            assert decision.target_stop_id is not None  # noqa: S101 (sanity)
            token = decision.target_stop_id
            pending.add_contribution(token, event)
            return token

        # CREATE: mint a fresh batch ref and an `after` anchor.
        ref = f"n{next_ref_index}"
        next_ref_index += 1
        if last_batch_ref is not None:
            after: str | None = last_batch_ref
        else:
            after = find_after_neighbor(route, event.time)
        ops.append(CreateStop(city=event.city, after=after, ref=ref))
        pending.register_batch_stop(ref, event.city)
        pending.add_contribution(ref, event)
        last_batch_ref = ref
        return ref

    # Walk legs in fragment order. Each leg = (from-departure event, to-arrival event).
    for leg in fragment.legs:
        from_event = Event(
            city=leg.from_,
            time=leg.departure_at,
            role=EventRole.DEPARTURE,
            travelers=travelers_t,
        )
        to_event = Event(
            city=leg.to,
            time=leg.arrival_at,
            role=EventRole.ARRIVAL,
            travelers=travelers_t,
        )

        from_handle = _resolve_event(from_event)
        to_handle = _resolve_event(to_event)

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


# --------------------------------------------------------------------------- #
# classify_event — the heart of Slice 3
# --------------------------------------------------------------------------- #


def classify_event(
    route: WorkingRoute,
    event: Event,
    *,
    pending: _PendingLedger | None = None,
    fragment_events: dict[str, list[datetime]] | None = None,
) -> Decision:
    """Decide CREATE-new vs ENRICH-existing for one city event.

    Faithful translation of the LLM prompt's three explicit conditions
    (see :data:`spikes.route_engine_llm.prompts.SYSTEM_PROMPT` — "STOP IDENTITY"
    section) plus the arrival-after-departure sanity check.

    Precedence (applied IN ORDER against the current route's same-city stops
    AND any contributions already pending in this batch — recorded in
    ``pending``):

    (a) City not in route → CREATE.
    (b) For each existing same-city stop S, if its projected time and
        ``event.time`` are strictly disjoint (one strictly before the other)
        AND at least one different-city stop's projected time lies strictly
        between them → S is a chronologically separate visit; CREATE.
        Bidirectional: the event may be later or earlier than S. The
        intervening different-city anchors include both real route stops AND
        OTHER cities in this same fragment (``fragment_events``) — the LLM
        prompt's Example B treats the fragment's own MAD/HEL between a new
        day-1 LHR and an existing day-4 LHR as proof of two distinct LHR
        visits, even though MAD/HEL aren't in the route yet.
    (c) Per-traveler slot already filled at S (for the event's role:
        arrival or departure) with a DIFFERENT time → CREATE. Same-time
        same-role for another traveler is not a conflict — that's enrichment.

    Else ENRICH the surviving contiguous same-city candidate (chronologically
    nearest if multiple survive). The arrival-after-departure sanity check
    flips a candidate ENRICH back to CREATE if folding the event into the
    target would invert that stop's own arrival/departure ordering.
    """
    pending = pending or _PendingLedger.from_route(route)
    fragment_events = fragment_events or {}

    # All same-city candidates (real route stops + batch stops not yet applied).
    candidates = _same_city_candidates(route, event.city, pending)

    if not candidates:
        # (a) city not in route AND not minted earlier this batch.
        return Decision.create()

    # All stops (any city) with a projected time — needed for condition (b)
    # "intervening different-city stop in time". Includes the fragment's OWN
    # other-city events because they too will land as stops in this batch.
    other_city_times = _other_city_times(route, pending, event.city)
    for other_city, times in fragment_events.items():
        if other_city == event.city:
            continue
        other_city_times.extend(times)

    surviving: list[_SameCityCandidate] = []
    for cand in candidates:
        if _condition_b_triggers(cand, event, other_city_times):
            continue  # CREATE per-candidate; check the rest
        if _condition_c_triggers(cand, event, pending):
            continue
        if _sanity_check_would_invert(cand, event):
            continue
        surviving.append(cand)

    if not surviving:
        return Decision.create()

    # Multiple contiguous same-city stops would be unusual — pick the
    # chronologically-nearest one (by projected time vs event.time).
    surviving.sort(key=lambda c: _time_distance(c.projected_time, event.time))
    chosen = surviving[0]
    return Decision.enrich(chosen.token)


# --------------------------------------------------------------------------- #
# Pending-projection ledger: in-batch overlay on top of `route`
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Contribution:
    """One in-batch event credited to a token (existing stop id or batch ref)."""

    role: EventRole
    time: datetime
    travelers: tuple[str, ...]


@dataclass(slots=True)
class _Pending:
    """Overlay state for a single token: its city + accumulated contributions.

    For an existing route stop the contributions are seeded from the route's
    real transits so that condition (c) sees pre-existing arrivals/departures
    as already-filled slots. For a batch ref the contributions start empty and
    are appended as we classify events for that ref.
    """

    city: str
    is_batch: bool
    contributions: list[_Contribution] = field(default_factory=list)

    def add(self, event: Event) -> None:
        self.contributions.append(
            _Contribution(role=event.role, time=event.time, travelers=event.travelers)
        )

    def projected_time(self) -> datetime | None:
        """Chronological position: earliest arrival; else earliest departure."""
        arrivals = [c.time for c in self.contributions if c.role is EventRole.ARRIVAL]
        if arrivals:
            return min(arrivals)
        departures = [
            c.time for c in self.contributions if c.role is EventRole.DEPARTURE
        ]
        if departures:
            return min(departures)
        return None

    def arrival_at(self) -> datetime | None:
        """Effective arrival projection (earliest arrival contribution, if any)."""
        arrivals = [c.time for c in self.contributions if c.role is EventRole.ARRIVAL]
        return min(arrivals) if arrivals else None

    def departure_at(self) -> datetime | None:
        """Effective departure projection (earliest departure contribution, if any)."""
        departures = [
            c.time for c in self.contributions if c.role is EventRole.DEPARTURE
        ]
        return min(departures) if departures else None


@dataclass(slots=True)
class _PendingLedger:
    """Per-batch overlay of pending contributions, keyed by token.

    A token is either an existing ``stop-N`` id (real route stop) or a
    batch-local ref (e.g. ``n1``) for a stop we are about to create. The
    ledger is built from the route at the start of a batch and updated as
    each event is classified.
    """

    pending: dict[str, _Pending] = field(default_factory=dict)
    # Insertion order of batch tokens (for chaining + iteration stability).
    batch_tokens: list[str] = field(default_factory=list)

    @classmethod
    def from_route(cls, route: WorkingRoute) -> _PendingLedger:
        """Seed the ledger with each existing stop's real-transit contributions."""
        ledger = cls()
        for stop in route.stops:
            entry = _Pending(city=stop.city, is_batch=False)
            # Seed from real transits so condition (c) sees pre-existing slots.
            for transit in route.transits:
                if transit.to_stop_id == stop.id:
                    entry.contributions.append(
                        _Contribution(
                            role=EventRole.ARRIVAL,
                            time=transit.arrival_at,
                            travelers=tuple(transit.travelers),
                        )
                    )
                if transit.from_stop_id == stop.id:
                    entry.contributions.append(
                        _Contribution(
                            role=EventRole.DEPARTURE,
                            time=transit.departure_at,
                            travelers=tuple(transit.travelers),
                        )
                    )
            ledger.pending[stop.id] = entry
        return ledger

    def register_batch_stop(self, ref: str, city: str) -> None:
        """Declare a new batch ref before its contributions are added."""
        self.pending[ref] = _Pending(city=city, is_batch=True)
        self.batch_tokens.append(ref)

    def add_contribution(self, token: str, event: Event) -> None:
        """Credit `event` to `token`'s pending entry (auto-seeding existing stops)."""
        entry = self.pending.get(token)
        if entry is None:
            # Should only happen for existing stop ids that somehow weren't
            # seeded — be defensive and create an empty entry.
            entry = _Pending(city=event.city, is_batch=False)
            self.pending[token] = entry
        entry.add(event)

    def iter_same_city(self, city: str) -> list[tuple[str, _Pending]]:
        """All `(token, entry)` pairs whose city matches `city`."""
        return [
            (token, entry)
            for token, entry in self.pending.items()
            if entry.city == city
        ]

    def iter_other_city_times(self, city: str) -> list[datetime]:
        """Projected times of every DIFFERENT-city token currently known."""
        times: list[datetime] = []
        for entry in self.pending.values():
            if entry.city == city:
                continue
            proj = entry.projected_time()
            if proj is not None:
                times.append(proj)
        return times


@dataclass(frozen=True, slots=True)
class _SameCityCandidate:
    """A same-city stop the event could ENRICH — either real or batch-local."""

    token: str  # existing stop id OR batch ref
    city: str
    arrival_at: datetime | None
    departure_at: datetime | None
    # The stop's projected position in time (for condition b + ordering).
    projected_time: datetime | None
    # Whether this candidate is a same-batch ref (vs an existing route stop).
    is_batch: bool


def _same_city_candidates(
    route: WorkingRoute, city: str, pending: _PendingLedger
) -> list[_SameCityCandidate]:
    """Collect all same-city candidates (real stops + same-batch refs).

    Real-stop projections are taken from the pending ledger (which seeds from
    the route's real transits AND folds in any in-batch contributions credited
    to that real stop so far), so the classifier sees the freshest possible
    timing for the next event in the batch.
    """
    out: list[_SameCityCandidate] = []
    for stop in route.stops:
        if stop.city != city:
            continue
        entry = pending.pending.get(stop.id)
        arrival, departure, projected = _combine_real_and_pending(stop, entry)
        out.append(
            _SameCityCandidate(
                token=stop.id,
                city=stop.city,
                arrival_at=arrival,
                departure_at=departure,
                projected_time=projected,
                is_batch=False,
            )
        )
    for token, entry in pending.iter_same_city(city):
        if not entry.is_batch:
            continue  # real stops handled above
        out.append(
            _SameCityCandidate(
                token=token,
                city=entry.city,
                arrival_at=entry.arrival_at(),
                departure_at=entry.departure_at(),
                projected_time=entry.projected_time(),
                is_batch=True,
            )
        )
    return out


def _combine_real_and_pending(
    stop: RouteStop, entry: _Pending | None
) -> tuple[datetime | None, datetime | None, datetime | None]:
    """Fold a real stop's explicit fields with the pending overlay's contributions.

    Returns (arrival_at, departure_at, projected_time). Explicit values on the
    real stop win for display; the pending overlay supplements with the
    earliest in-batch contribution when the real value is unset.
    """
    pending_arrival = entry.arrival_at() if entry is not None else None
    pending_departure = entry.departure_at() if entry is not None else None
    arrival = stop.arrival_at or pending_arrival
    departure = stop.departure_at or pending_departure
    projected = arrival or departure
    return arrival, departure, projected


def _other_city_times(
    route: WorkingRoute, pending: _PendingLedger, city: str
) -> list[datetime]:
    """Projected times of every DIFFERENT-city stop currently known.

    Combines explicit stop timing on the route with the pending ledger so
    that an in-batch enrichment is visible as a between-time anchor.
    """
    times: list[datetime] = []
    for stop in route.stops:
        if stop.city == city:
            continue
        entry = pending.pending.get(stop.id)
        _arr, _dep, proj = _combine_real_and_pending(stop, entry)
        if proj is not None:
            times.append(proj)
    # Batch-only tokens (not backed by a real route stop yet).
    for token, entry in pending.pending.items():
        if entry.city == city or not entry.is_batch:
            continue
        proj = entry.projected_time()
        if proj is not None:
            times.append(proj)
    return times


def _condition_b_triggers(
    cand: _SameCityCandidate,
    event: Event,
    other_city_times: list[datetime],
) -> bool:
    """Chronologically disjoint with an intervening different-city stop in time.

    Bidirectional: triggers whether the event is strictly later than `cand`
    or strictly earlier — provided some different-city stop's projected time
    sits strictly between the two.
    """
    cand_time = cand.projected_time
    if cand_time is None:
        return False  # no temporal signal — fall through to (c)/enrich

    if cand_time == event.time:
        return False

    lo, hi = (
        (cand_time, event.time) if cand_time < event.time else (event.time, cand_time)
    )
    return any(lo < t < hi for t in other_city_times)


def _condition_c_triggers(
    cand: _SameCityCandidate, event: Event, pending: _PendingLedger
) -> bool:
    """Per-traveler slot already filled at `cand` for this event's role.

    The slot is "filled" when ANY pending contribution at `cand` (seeded from
    real transits AND grown by in-batch enrichments) shares the event's role,
    overlaps the event's travelers, and has a different time. Same-role
    same-time for an additional traveler is NOT a conflict (it's enrichment).
    """
    entry = pending.pending.get(cand.token)
    if entry is None:
        return False
    event_travelers = set(event.travelers)
    for contrib in entry.contributions:
        if contrib.role is not event.role:
            continue
        if contrib.time == event.time:
            continue
        if not event_travelers.intersection(contrib.travelers):
            continue
        return True
    return False


def _sanity_check_would_invert(cand: _SameCityCandidate, event: Event) -> bool:
    """Whether ENRICHing `cand` with `event` would make arrival > departure on it."""
    if event.role is EventRole.ARRIVAL:
        new_arrival = event.time
        existing_departure = cand.departure_at
        if existing_departure is not None and new_arrival > existing_departure:
            return True
    elif event.role is EventRole.DEPARTURE:
        new_departure = event.time
        existing_arrival = cand.arrival_at
        if existing_arrival is not None and existing_arrival > new_departure:
            return True
    return False


def _time_distance(a: datetime | None, b: datetime) -> float:
    """Distance for the nearest-candidate tiebreak; untimed sort to the end."""
    if a is None:
        return float("inf")
    return abs((a - b).total_seconds())


def _fragment_events(
    fragment: TransitTicketFragment, _travelers: tuple[str, ...]
) -> dict[str, list[datetime]]:
    """Pre-collect every (city -> [times]) the fragment will emit.

    Used by :func:`classify_event` to populate condition (b)'s set of
    different-city time anchors with the fragment's OWN cities, not just the
    cities already present on the route. This is what lets a multi-leg
    fragment like SVO -> LHR -> DUB -> LHR see DUB (its own different-city
    leg, not yet in the route) as proof that the two LHR events are distinct
    visits — matching the LLM prompt's Example A reasoning within one ticket.
    """
    by_city: dict[str, list[datetime]] = {}
    for leg in fragment.legs:
        by_city.setdefault(leg.from_, []).append(leg.departure_at)
        by_city.setdefault(leg.to, []).append(leg.arrival_at)
    return by_city


# --------------------------------------------------------------------------- #
# Chronological anchoring for new stops
# --------------------------------------------------------------------------- #


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

    timed: list[tuple[datetime, RouteStop]] = []
    for stop in route.stops:
        proj = _projected_time(stop)
        if proj is not None:
            timed.append((proj, stop))

    if not timed:
        return route.stops[-1].id

    timed.sort(key=lambda pair: pair[0])

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
