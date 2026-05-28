"""Build per-run artifacts from scored scenario results (pure, offline).

Two outputs per run (technical-considerations §2.5):

- ``results.json`` — the full machine-readable per-scenario record. It is the
  source the later cross-model ``compare.md`` (Slice 5) reads, so it carries
  everything: pass/fail, the failure-category bucket, per-fragment latencies,
  summed token usage, and estimated cost.
- ``report.md`` — a human summary: headline route accuracy under the
  all-three-checks gate, a per-axis accuracy breakdown (shape / pax / ordering /
  return / hotels parsed from scenario names), latency p50/p95/mean per
  scenario, total + per-scenario cost, and failure COUNTS bucketed by which
  check failed (no per-scenario diffs).

Everything here is a **pure function of the results list** — no network, no
filesystem reads, no ``anthropic`` import — so it is fully unit-testable
offline. The runner is the only caller that writes these strings to disk.
"""

from __future__ import annotations

import json
import math
import re
import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from spikes.route_engine_llm.bedrock_client import Usage

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

__all__ = [
    "ScenarioAxes",
    "ScenarioResult",
    "RunMeta",
    "parse_axes",
    "build_results_json",
    "build_report_md",
]


# --------------------------------------------------------------------------- #
# Per-scenario result record (the runner produces these; tests synthesize them)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    """One scenario's scored outcome plus its measured latency / usage / cost.

    ``failure_category`` is the ``FailureCategory`` value (a string) when
    ``passed`` is False, else None. ``error`` carries the engine-error message
    for scenarios that aborted mid-replay (those are always ``passed=False``).
    ``fragment_latencies`` is one wall-clock seconds figure per ``update_route``
    call, in replay order. ``usage`` is the summed token usage across the
    scenario's calls. ``cost_usd`` is the estimated USD cost for the scenario.
    """

    name: str
    passed: bool
    failure_category: str | None = None
    fragment_latencies: list[float] = field(default_factory=list)
    usage: Usage = field(default_factory=lambda: Usage(0, 0))
    cost_usd: float = 0.0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RunMeta:
    """Run-level metadata stamped into both artifacts."""

    model: str
    model_id: str
    started_at: datetime
    pricing_as_of: str
    pricing_source: str
    accuracy_bar: float = 0.99


# --------------------------------------------------------------------------- #
# Axis parsing from scenario names
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ScenarioAxes:
    """The evaluation axes encoded in a scenario name.

    Name grammar: ``NNN-<shape>-<pax>p-<order>[-return][-hotels]`` where
    ``shape`` ∈ straight|circle|star, ``order`` ∈
    forward|reverse|bisect|seeded-shuffle, and ``return`` / ``hotels`` are
    optional trailing flags (in that order).
    """

    shape: str
    pax: int
    ordering: str
    has_return: bool
    has_hotels: bool


# Anchored grammar; `order` is non-greedy up to the optional trailing flags so
# `seeded-shuffle` (which itself contains a hyphen) is captured whole.
_NAME_RE = re.compile(
    r"^\d+-(?P<shape>straight|circle|star)-(?P<pax>\d+)p-"
    r"(?P<order>forward|reverse|bisect|seeded-shuffle)"
    r"(?P<ret>-return)?(?P<hotels>-hotels)?$"
)


def parse_axes(name: str) -> ScenarioAxes:
    """Parse a scenario name into its :class:`ScenarioAxes`.

    Raises ``ValueError`` on a name that does not match the corpus grammar, so a
    renamed/typo'd scenario fails loudly rather than skewing the breakdown.
    """
    match = _NAME_RE.match(name)
    if match is None:
        msg = f"scenario name does not match the corpus grammar: {name!r}"
        raise ValueError(msg)
    return ScenarioAxes(
        shape=match["shape"],
        pax=int(match["pax"]),
        ordering=match["order"],
        has_return=match["ret"] is not None,
        has_hotels=match["hotels"] is not None,
    )


# --------------------------------------------------------------------------- #
# Aggregation helpers (pure)
# --------------------------------------------------------------------------- #


def _percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile of ``values`` (``pct`` in [0, 100]).

    Returns 0.0 for an empty input. Nearest-rank (rather than interpolated) is
    used so a single-sample scenario reports that sample for every percentile.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


@dataclass(frozen=True, slots=True)
class _LatencyStats:
    p50: float
    p95: float
    mean: float

    @classmethod
    def of(cls, latencies: Sequence[float]) -> _LatencyStats:
        if not latencies:
            return cls(0.0, 0.0, 0.0)
        return cls(
            p50=_percentile(latencies, 50),
            p95=_percentile(latencies, 95),
            mean=statistics.fmean(latencies),
        )


def _accuracy(results: Sequence[ScenarioResult]) -> tuple[int, int, float]:
    """Return (passed, total, fraction-passed) for ``results`` (0.0 if empty)."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    fraction = passed / total if total else 0.0
    return passed, total, fraction


def _axis_breakdown(
    results: Sequence[ScenarioResult],
    key: str,
) -> dict[str, tuple[int, int, float]]:
    """Group accuracy by one axis attribute of :class:`ScenarioAxes`.

    Keys are stringified attribute values (e.g. ``"circle"``, ``"2"``,
    ``"true"``); values are (passed, total, fraction). Buckets are returned in
    sorted-key order for stable report output.
    """
    buckets: dict[str, list[ScenarioResult]] = {}
    for result in results:
        axes = parse_axes(result.name)
        value = getattr(axes, key)
        label = _label(value)
        buckets.setdefault(label, []).append(result)
    return {label: _accuracy(group) for label, group in sorted(buckets.items())}


def _label(value: object) -> str:
    """Stable string label for an axis value (lowercase bools, plain ints)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _failure_buckets(results: Sequence[ScenarioResult]) -> dict[str, int]:
    """Count failures by ``failure_category`` (counts only, no diffs).

    Failed scenarios with no category (an engine error aborted the replay before
    scoring) are bucketed under ``"engine_error"``.
    """
    counts: dict[str, int] = {}
    for result in results:
        if result.passed:
            continue
        bucket = result.failure_category or "engine_error"
        counts[bucket] = counts.get(bucket, 0) + 1
    return counts


# --------------------------------------------------------------------------- #
# results.json
# --------------------------------------------------------------------------- #


def _usage_dict(usage: Usage) -> dict[str, int]:
    return {
        "inputTokens": usage.input_tokens,
        "outputTokens": usage.output_tokens,
        "cacheCreationInputTokens": usage.cache_creation_input_tokens,
        "cacheReadInputTokens": usage.cache_read_input_tokens,
    }


def _result_dict(result: ScenarioResult) -> dict[str, object]:
    axes = parse_axes(result.name)
    latency = _LatencyStats.of(result.fragment_latencies)
    return {
        "name": result.name,
        "passed": result.passed,
        "failureCategory": result.failure_category,
        "error": result.error,
        "axes": {
            "shape": axes.shape,
            "pax": axes.pax,
            "ordering": axes.ordering,
            "hasReturn": axes.has_return,
            "hasHotels": axes.has_hotels,
        },
        "fragmentLatencies": list(result.fragment_latencies),
        "latency": {"p50": latency.p50, "p95": latency.p95, "mean": latency.mean},
        "usage": _usage_dict(result.usage),
        "costUsd": result.cost_usd,
    }


def build_results_json(meta: RunMeta, results: Sequence[ScenarioResult]) -> str:
    """Serialize the full per-scenario record as pretty-printed JSON.

    Machine-readable and stable: the later cross-model compare reads this.
    """
    passed, total, fraction = _accuracy(results)
    all_latencies = [lat for r in results for lat in r.fragment_latencies]
    overall_latency = _LatencyStats.of(all_latencies)
    total_cost = sum(r.cost_usd for r in results)
    payload: dict[str, object] = {
        "model": meta.model,
        "modelId": meta.model_id,
        "startedAt": meta.started_at.astimezone(UTC).isoformat(),
        "pricing": {"asOf": meta.pricing_as_of, "source": meta.pricing_source},
        "accuracyBar": meta.accuracy_bar,
        "summary": {
            "scenarios": total,
            "passed": passed,
            "accuracy": fraction,
            "totalCostUsd": total_cost,
            "latency": {
                "p50": overall_latency.p50,
                "p95": overall_latency.p95,
                "mean": overall_latency.mean,
            },
            "failureBuckets": _failure_buckets(results),
        },
        "scenarios": [_result_dict(r) for r in results],
    }
    return json.dumps(payload, indent=2, sort_keys=False) + "\n"


# --------------------------------------------------------------------------- #
# report.md
# --------------------------------------------------------------------------- #

_AXIS_TITLES: list[tuple[str, str]] = [
    ("shape", "Shape"),
    ("pax", "Travelers"),
    ("ordering", "Ordering"),
    ("has_return", "Return"),
    ("has_hotels", "Hotels"),
]


def _fmt_pct(fraction: float) -> str:
    return f"{fraction * 100:.1f}%"


def _fmt_usd(amount: float) -> str:
    # Cost-per-scenario is tiny; six decimals keeps sub-cent figures legible.
    return f"${amount:.6f}"


def _fmt_secs(seconds: float) -> str:
    return f"{seconds:.3f}s"


def build_report_md(meta: RunMeta, results: Sequence[ScenarioResult]) -> str:
    """Render the human-readable ``report.md`` for a run."""
    passed, total, fraction = _accuracy(results)
    total_cost = sum(r.cost_usd for r in results)
    failure_buckets = _failure_buckets(results)
    lines: list[str] = []

    lines.append(f"# Engine Spike Run — {meta.model}")
    lines.append("")
    lines.append(f"- Model alias: `{meta.model}`")
    lines.append(f"- Model id: `{meta.model_id}`")
    lines.append(f"- Started: {meta.started_at.astimezone(UTC).isoformat()}")
    lines.append(
        f"- Pricing: estimate as of {meta.pricing_as_of} "
        f"(source: {meta.pricing_source})"
    )
    lines.append("")

    # Headline accuracy under the all-three-checks gate.
    bar = _fmt_pct(meta.accuracy_bar)
    clears = "yes" if total and fraction >= meta.accuracy_bar else "no"
    lines.append("## Route accuracy")
    lines.append("")
    lines.append(
        f"**{_fmt_pct(fraction)}** ({passed}/{total} scenarios passed the "
        f"all-three-checks gate)."
    )
    lines.append("")
    lines.append(f"Clears the >={bar} bar: **{clears}**.")
    lines.append("")

    # Per-axis breakdown.
    lines.append("## Per-axis accuracy")
    lines.append("")
    for key, title in _AXIS_TITLES:
        lines.append(f"### {title}")
        lines.append("")
        lines.append("| value | passed | total | accuracy |")
        lines.append("| --- | --: | --: | --: |")
        for label, (a_pass, a_total, a_frac) in _axis_breakdown(results, key).items():
            lines.append(f"| {label} | {a_pass} | {a_total} | {_fmt_pct(a_frac)} |")
        lines.append("")

    # Failure buckets — counts only.
    lines.append("## Failure buckets (counts only)")
    lines.append("")
    if failure_buckets:
        lines.append("| bucket | count |")
        lines.append("| --- | --: |")
        for bucket, count in sorted(failure_buckets.items()):
            lines.append(f"| {bucket} | {count} |")
    else:
        lines.append("No failures.")
    lines.append("")

    # Cost.
    lines.append("## Cost (estimated)")
    lines.append("")
    lines.append(f"Total: **{_fmt_usd(total_cost)}** across {total} scenarios.")
    lines.append("")

    # Per-scenario latency + cost.
    lines.append("## Per-scenario latency & cost")
    lines.append("")
    lines.append(
        "| scenario | result | latency p50 | latency p95 | latency mean | cost |"
    )
    lines.append("| --- | --- | --: | --: | --: | --: |")
    for result in results:
        latency = _LatencyStats.of(result.fragment_latencies)
        outcome = (
            "pass" if result.passed else (result.failure_category or "engine_error")
        )
        lines.append(
            f"| {result.name} | {outcome} | {_fmt_secs(latency.p50)} | "
            f"{_fmt_secs(latency.p95)} | {_fmt_secs(latency.mean)} | "
            f"{_fmt_usd(result.cost_usd)} |"
        )
    lines.append("")

    return "\n".join(lines)
