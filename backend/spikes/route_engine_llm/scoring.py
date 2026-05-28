"""Scoring: the three pass/fail checks from technical-considerations §2.4.

Each check returns a structured :class:`CheckResult` (pass/fail + a failure
category + a human-readable reason). :func:`score_scenario` combines them: a
scenario passes only if all three hold.

The three checks:

- :func:`final_route_match` — the final route structurally equals the expected
  route. Engine IDs are stripped; timestamps are canonicalized to UTC ISO-8601
  and traveler lists are sorted. ``stops`` are compared as an ordered SEQUENCE;
  ``transits`` as a SET, with engine stop IDs resolved back to city codes so
  they line up with the expected route's ``from``/``to``.
- :func:`identity_preserved` — across the ordered per-step snapshots the set of
  stop IDs is APPEND-ONLY (an ID present at step N survives to every later
  step) and each ID's ``city`` never changes.
- :func:`ordering_consistent` — using the FINAL snapshot as canonical order, in
  every earlier snapshot the known stops appear as an order-preserving
  subsequence of their final positions (gap tolerance).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from spikes.route_engine_llm.corpus import ExpectedRoute, ExpectedStop, ExpectedTransit
from spikes.route_engine_llm.models import Accommodation, RouteStop, WorkingRoute

__all__ = [
    "FailureCategory",
    "CheckResult",
    "final_route_match",
    "identity_preserved",
    "ordering_consistent",
    "score_scenario",
]


class FailureCategory(StrEnum):
    """Counts-only failure buckets reported when a check fails."""

    FINAL_MISMATCH = "final_mismatch"
    IDENTITY_VIOLATION = "identity_violation"
    ORDERING_VIOLATION = "ordering_violation"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Outcome of a single check or the aggregate scenario score."""

    passed: bool
    category: FailureCategory | None = None
    reason: str | None = None

    @classmethod
    def ok(cls) -> CheckResult:
        """A passing result."""
        return cls(passed=True)

    @classmethod
    def fail(cls, category: FailureCategory, reason: str) -> CheckResult:
        """A failing result with a category bucket and a reason."""
        return cls(passed=False, category=category, reason=reason)


# --------------------------------------------------------------------------- #
# Normalization helpers
# --------------------------------------------------------------------------- #

# Canonical comparable forms (hashable) for the fields expected-route carries.
type _Stamp = str | None
type _AccomKey = tuple[_Stamp, _Stamp, str | None]
type _StopKey = tuple[str, _Stamp, _Stamp, tuple[str, ...], tuple[_AccomKey, ...]]
type _TransitKey = tuple[str, str, str, _Stamp, _Stamp, tuple[str, ...], str]


def _stamp(value: datetime | None) -> _Stamp:
    """Canonicalize a datetime to a comparable UTC ISO-8601 string (or None).

    Pydantic parses trailing ``Z`` into a tz-aware datetime; coercing to UTC
    makes values with different source offsets compare equal when they denote
    the same instant.
    """
    if value is None:
        return None
    return value.astimezone(UTC).isoformat()


def _travelers(values: list[str]) -> tuple[str, ...]:
    """Sort travelers so order never affects equality."""
    return tuple(sorted(values))


def _accom_key(accom: Accommodation) -> _AccomKey:
    return (_stamp(accom.check_in_at), _stamp(accom.check_out_at), accom.hotel_name)


def _accoms(accoms: list[Accommodation]) -> tuple[_AccomKey, ...]:
    # Accommodations on a stop are order-insensitive: sort their keys.
    return tuple(sorted(_accom_key(a) for a in accoms))


def _working_stop_key(stop: RouteStop) -> _StopKey:
    return (
        stop.city,
        _stamp(stop.arrival_at),
        _stamp(stop.departure_at),
        _travelers(stop.travelers),
        _accoms(stop.accommodations),
    )


def _expected_stop_key(stop: ExpectedStop) -> _StopKey:
    return (
        stop.city,
        _stamp(stop.arrival_at),
        _stamp(stop.departure_at),
        _travelers(stop.travelers),
        _accoms(stop.accommodations),
    )


def _working_transit_keys(route: WorkingRoute) -> list[_TransitKey]:
    """Normalized transit keys with stop IDs resolved to city codes."""
    keys: list[_TransitKey] = []
    for transit in route.transits:
        from_stop = route.stop_by_id(transit.from_stop_id)
        to_stop = route.stop_by_id(transit.to_stop_id)
        if from_stop is None or to_stop is None:
            # Dangling reference — represent it distinctly so it can never
            # match a valid expected transit.
            from_city = from_stop.city if from_stop else f"?{transit.from_stop_id}"
            to_city = to_stop.city if to_stop else f"?{transit.to_stop_id}"
        else:
            from_city = from_stop.city
            to_city = to_stop.city
        keys.append(
            (
                from_city,
                to_city,
                str(transit.mode),
                _stamp(transit.departure_at),
                _stamp(transit.arrival_at),
                _travelers(transit.travelers),
                transit.source_fragment_id,
            )
        )
    return keys


def _expected_transit_key(transit: ExpectedTransit) -> _TransitKey:
    return (
        transit.from_,
        transit.to,
        str(transit.mode),
        _stamp(transit.departure_at),
        _stamp(transit.arrival_at),
        _travelers(transit.travelers),
        transit.source_fragment_id,
    )


# --------------------------------------------------------------------------- #
# Check 1: final-route structural match
# --------------------------------------------------------------------------- #


def final_route_match(working: WorkingRoute, expected: ExpectedRoute) -> CheckResult:
    """Whether the final working route structurally equals the expected route.

    Stops are compared as an ordered sequence; transits as a set. Engine IDs
    are stripped, timestamps canonicalized to UTC, traveler lists sorted.
    """
    working_stops = [_working_stop_key(s) for s in working.stops]
    expected_stops = [_expected_stop_key(s) for s in expected.stops]
    if working_stops != expected_stops:
        return CheckResult.fail(
            FailureCategory.FINAL_MISMATCH,
            f"stop sequence differs: got {working_stops!r}, "
            f"expected {expected_stops!r}",
        )

    working_transits = sorted(_working_transit_keys(working))
    expected_transits = sorted(_expected_transit_key(t) for t in expected.transits)
    if working_transits != expected_transits:
        return CheckResult.fail(
            FailureCategory.FINAL_MISMATCH,
            f"transit set differs: got {working_transits!r}, "
            f"expected {expected_transits!r}",
        )

    return CheckResult.ok()


# --------------------------------------------------------------------------- #
# Check 2: identity preservation (append-only, stable city)
# --------------------------------------------------------------------------- #


def identity_preserved(snapshots: list[WorkingRoute]) -> CheckResult:
    """Whether stop identity is append-only with a stable city across steps.

    An ID present at step N must persist to every later step, and a given ID's
    ``city`` must never change.
    """
    seen_city: dict[str, str] = {}
    prev_ids: set[str] = set()

    for step, snapshot in enumerate(snapshots):
        current_ids = set(snapshot.stop_ids())

        missing = prev_ids - current_ids
        if missing:
            return CheckResult.fail(
                FailureCategory.IDENTITY_VIOLATION,
                f"stop id(s) {sorted(missing)!r} present before step {step} "
                f"disappeared",
            )

        for stop in snapshot.stops:
            known_city = seen_city.get(stop.id)
            if known_city is not None and known_city != stop.city:
                return CheckResult.fail(
                    FailureCategory.IDENTITY_VIOLATION,
                    f"stop id {stop.id!r} changed city from {known_city!r} "
                    f"to {stop.city!r} at step {step}",
                )
            seen_city[stop.id] = stop.city

        prev_ids = current_ids

    return CheckResult.ok()


# --------------------------------------------------------------------------- #
# Check 3: ordering consistency (gap tolerance)
# --------------------------------------------------------------------------- #


def ordering_consistent(snapshots: list[WorkingRoute]) -> CheckResult:
    """Whether earlier snapshots are order-preserving subsequences of the final.

    Each stop ID is mapped to its index in the FINAL snapshot. In every earlier
    snapshot the indices of stops that exist in the final route must be strictly
    increasing — known cities are never reordered to close a gap.
    """
    if not snapshots:
        return CheckResult.ok()

    final = snapshots[-1]
    final_index = {stop.id: i for i, stop in enumerate(final.stops)}

    for step, snapshot in enumerate(snapshots[:-1]):
        last_index = -1
        for stop in snapshot.stops:
            index = final_index.get(stop.id)
            if index is None:
                # A stop that vanished by the final snapshot is an identity
                # concern, not an ordering one; skip it here.
                continue
            if index <= last_index:
                return CheckResult.fail(
                    FailureCategory.ORDERING_VIOLATION,
                    f"stop id {stop.id!r} at step {step} breaks final order "
                    f"(final index {index} <= previous {last_index})",
                )
            last_index = index

    return CheckResult.ok()


# --------------------------------------------------------------------------- #
# Aggregate
# --------------------------------------------------------------------------- #


def score_scenario(
    snapshots: list[WorkingRoute], expected: ExpectedRoute
) -> CheckResult:
    """Aggregate the three checks; pass only if all hold.

    The final route is taken from the last snapshot. On failure, the first
    failing check's category and reason are surfaced.
    """
    if not snapshots:
        return CheckResult.fail(
            FailureCategory.FINAL_MISMATCH,
            "no snapshots produced for scenario",
        )

    identity = identity_preserved(snapshots)
    if not identity.passed:
        return identity

    ordering = ordering_consistent(snapshots)
    if not ordering.passed:
        return ordering

    return final_route_match(snapshots[-1], expected)
