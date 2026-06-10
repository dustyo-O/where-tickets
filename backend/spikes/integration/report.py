"""Pure rendering helpers for the integration runner.

DUS-31 Slice 7. Mirrors the layout of ``corpus/pdf/runner.py``'s rendering
helpers: each function returns either a human-readable string or a
JSON-serializable dict. No I/O, no ``print``, no ``Path.write_text`` — the
runner owns all I/O so these functions stay testable in isolation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from spikes.route_engine_llm.scoring import CheckResult

if TYPE_CHECKING:  # pragma: no cover - typing only
    from spikes.route_engine_llm.corpus import ExpectedRoute
    from spikes.route_engine_llm.models import WorkingRoute

__all__ = [
    "DocumentOutcome",
    "TripResult",
    "build_report_dict",
    "render_route_diff",
    "render_run_summary",
    "render_trip_summary",
]


@dataclass(slots=True)
class DocumentOutcome:
    """Per-document outcome inside a trip.

    The runner records one entry per manifest document; failures (extraction,
    adapter, engine) populate ``error`` and skip the downstream stages.
    """

    pdf: str
    extracted: bool = False
    adapted: bool = False
    folded: bool = False
    extraction_path: str | None = None
    pdf_kind: str | None = None
    error: str | None = None
    latency_ms: float | None = None


@dataclass(slots=True)
class TripResult:
    """Per-trip outcome the runner produces and the report renders.

    ``passed`` is the final boolean. ``failures`` collects all error messages
    encountered (extraction, adapter, engine, scoring) so a single trip can
    surface multiple failures without short-circuiting. ``scoring_reason`` is
    the ``CheckResult.reason`` when ``final_route_match`` is the failing step.
    """

    slug: str
    passed: bool = False
    documents: list[DocumentOutcome] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    scoring_reason: str | None = None
    latency_ms_total: float = 0.0
    skipped_route_check: bool = False


def _check_to_reason(check: CheckResult) -> str | None:
    """Return the human-readable reason from a CheckResult, or ``None``."""
    return check.reason if not check.passed else None


# --------------------------------------------------------------------------- #
# Human-readable rendering
# --------------------------------------------------------------------------- #


def render_trip_summary(result: TripResult) -> str:
    """Render one trip's outcome as a multi-line human-readable block."""
    headline = "PASS" if result.passed else "FAIL"
    lines: list[str] = [f"{headline}  {result.slug}"]
    for doc in result.documents:
        status = _document_status_token(doc)
        path_bits: list[str] = []
        if doc.pdf_kind is not None:
            path_bits.append(f"kind={doc.pdf_kind}")
        if doc.extraction_path is not None:
            path_bits.append(f"path={doc.extraction_path}")
        if doc.latency_ms is not None:
            path_bits.append(f"ms={doc.latency_ms:.0f}")
        tail = ("  " + " ".join(path_bits)) if path_bits else ""
        line = f"  {status}  {doc.pdf}{tail}"
        if doc.error is not None:
            line += f"  -- {doc.error}"
        lines.append(line)
    for failure in result.failures:
        # Skip per-document errors that are already shown on the document line.
        if any(failure == d.error for d in result.documents):
            continue
        lines.append(f"  !!  {failure}")
    if result.scoring_reason is not None:
        lines.append(f"  scoring: {result.scoring_reason}")
    return "\n".join(lines)


def _document_status_token(doc: DocumentOutcome) -> str:
    if doc.error is not None:
        return "FAIL"
    if doc.folded:
        return "OK  "
    if doc.adapted:
        return "ADAPT"
    if doc.extracted:
        return "EXTR"
    return "SKIP"


def render_run_summary(results: list[TripResult]) -> str:
    """Render the aggregate ``N/M trips PASS`` line."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    return f"{passed}/{total} trips PASS"


def render_route_diff(expected: ExpectedRoute, actual: WorkingRoute) -> str:
    """Render a human-readable diff between an expected and an actual route.

    Called when ``final_route_match`` fails. The headline is the
    ``CheckResult.reason``; the body walks expected vs actual stops and
    transits side by side so the engineer can spot the mismatch without
    rerunning. Pure string work — no I/O.
    """
    lines: list[str] = ["expected route:"]
    for index, stop in enumerate(expected.stops):
        lines.append(
            f"  stop[{index}] {stop.city}  arr={_fmt_dt(stop.arrival_at)}  "
            f"dep={_fmt_dt(stop.departure_at)}  travelers={stop.travelers!r}"
        )
    for index, transit in enumerate(expected.transits):
        lines.append(
            f"  transit[{index}] {transit.from_} -> {transit.to}  "
            f"mode={transit.mode!s}  dep={_fmt_dt(transit.departure_at)}  "
            f"arr={_fmt_dt(transit.arrival_at)}  "
            f"src={transit.source_fragment_id}"
        )
    lines.append("actual route:")
    for index, stop in enumerate(actual.stops):
        lines.append(
            f"  stop[{index}] {stop.city}  arr={_fmt_dt(stop.arrival_at)}  "
            f"dep={_fmt_dt(stop.departure_at)}  travelers={stop.travelers!r}"
        )
    for index, transit in enumerate(actual.transits):
        from_stop = actual.stop_by_id(transit.from_stop_id)
        to_stop = actual.stop_by_id(transit.to_stop_id)
        from_city = (
            from_stop.city if from_stop is not None else f"?{transit.from_stop_id}"
        )
        to_city = to_stop.city if to_stop is not None else f"?{transit.to_stop_id}"
        lines.append(
            f"  transit[{index}] {from_city} -> {to_city}  "
            f"mode={transit.mode!s}  dep={_fmt_dt(transit.departure_at)}  "
            f"arr={_fmt_dt(transit.arrival_at)}  "
            f"src={transit.source_fragment_id}"
        )
    return "\n".join(lines)


def _fmt_dt(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.astimezone(UTC).isoformat()


# --------------------------------------------------------------------------- #
# JSON-serializable report
# --------------------------------------------------------------------------- #


def build_report_dict(results: list[TripResult]) -> dict[str, object]:
    """Return a JSON-serializable summary of the run.

    Shape::

        {
          "version": 1,
          "summary": {"total": N, "passed": M, "failed": N-M},
          "trips": [
            {
              "slug": str,
              "passed": bool,
              "failures": [str, ...],
              "scoring_reason": str | None,
              "latency_ms_total": float,
              "skipped_route_check": bool,
              "documents": [
                {"pdf": str, "extracted": bool, ...},
                ...
              ]
            },
            ...
          ]
        }
    """
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    return {
        "version": 1,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": total - passed,
        },
        "trips": [_trip_to_dict(r) for r in results],
    }


def _trip_to_dict(result: TripResult) -> dict[str, object]:
    payload = asdict(result)
    # ``asdict`` already walks the dataclass; nothing extra needed.
    return payload
