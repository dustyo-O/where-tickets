"""Cross-model comparison report (``compare.md``) — pure + offline (Slice 5).

Each per-model run writes a ``results.json`` (schema produced by
:func:`spikes.route_engine_llm.report.build_results_json`). This module reads
2+ of those loaded payloads — ideally each over the full corpus — and renders a
single Markdown comparison that feeds the DUS-21 ADR:

- a **headline table** (one row per model: route accuracy, passed/total,
  latency mean/p50/p95, total cost, and whether it clears the ``accuracyBar``);
- **per-axis accuracy tables** (shape / pax / ordering / return / hotels) with
  models as columns so they line up directly (missing cells render ``—``);
- a **failure-bucket comparison** (counts per bucket per model);
- a **recommendation** section: which model(s) clear >=99%, a suggested v1
  engine if one is clearly best, or an explicit "no model clears the bar" with
  the algorithmic spike (DUS-20) named as the fallback.

Everything here is a **pure function of the loaded payloads** — no network, no
``anthropic`` import. The CLI is the only caller that reads the JSON files off
disk and writes the rendered string. ``build_compare_md`` recomputes per-axis
accuracy from each payload's ``scenarios[]`` (reusing the run's own ``axes``
block) rather than re-parsing names, so it stays in lockstep with the producer.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

__all__ = [
    "build_compare_md",
    "build_parser",
    "main",
]


# --------------------------------------------------------------------------- #
# Per-axis definitions: (axes-key in results.json, column/section title)
# --------------------------------------------------------------------------- #

# The `axes` block in each scenario record uses camelCase keys (see
# report._result_dict). Each tuple is (json key, human title).
_AXES: list[tuple[str, str]] = [
    ("shape", "Shape"),
    ("pax", "Travelers"),
    ("ordering", "Ordering"),
    ("hasReturn", "Return"),
    ("hasHotels", "Hotels"),
]


@dataclass(frozen=True, slots=True)
class _ModelView:
    """A flattened, defensively-read view of one loaded ``results.json``.

    Reading via ``.get`` with fallbacks keeps the comparer robust to a partial
    or hand-written payload (the synthetic test fixtures lean on this), while
    still matching the real producer's schema exactly.
    """

    model: str
    accuracy: float
    passed: int
    scenarios: int
    clears_bar: bool
    accuracy_bar: float
    mean_latency: float
    p50_latency: float
    p95_latency: float
    total_cost: float
    failure_buckets: dict[str, int]
    records: list[dict[str, Any]]

    @classmethod
    def of(cls, payload: dict[str, Any]) -> _ModelView:
        summary = payload.get("summary", {})
        latency = summary.get("latency", {})
        scenarios = int(summary.get("scenarios", 0))
        passed = int(summary.get("passed", 0))
        accuracy = float(summary.get("accuracy", 0.0))
        bar = float(payload.get("accuracyBar", 0.99))
        return cls(
            model=str(payload.get("model", "?")),
            accuracy=accuracy,
            passed=passed,
            scenarios=scenarios,
            # An empty run (0 scenarios) never "clears" the bar.
            clears_bar=bool(scenarios) and accuracy >= bar,
            accuracy_bar=bar,
            mean_latency=float(latency.get("mean", 0.0)),
            p50_latency=float(latency.get("p50", 0.0)),
            p95_latency=float(latency.get("p95", 0.0)),
            total_cost=float(summary.get("totalCostUsd", 0.0)),
            failure_buckets=dict(summary.get("failureBuckets", {})),
            records=list(payload.get("scenarios", [])),
        )


# --------------------------------------------------------------------------- #
# Formatting helpers (mirror report.py so the two artifacts read alike)
# --------------------------------------------------------------------------- #


def _fmt_pct(fraction: float) -> str:
    return f"{fraction * 100:.1f}%"


def _fmt_usd(amount: float) -> str:
    return f"${amount:.6f}"


def _fmt_secs(seconds: float) -> str:
    return f"{seconds:.3f}s"


# --------------------------------------------------------------------------- #
# Per-axis accuracy recomputed from each model's scenario records
# --------------------------------------------------------------------------- #


def _label(value: Any) -> str:
    """Stable string label for an axis value (lowercase bools, plain ints)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _axis_accuracy(view: _ModelView, axis_key: str) -> dict[str, float]:
    """Map each value of one axis to that model's pass fraction for it.

    Reads each record's ``axes`` block (camelCase, as written by the producer);
    records missing that axis are skipped so a sparse/partial payload does not
    crash the comparison.
    """
    buckets: dict[str, list[bool]] = {}
    for record in view.records:
        axes = record.get("axes")
        if not isinstance(axes, dict) or axis_key not in axes:
            continue
        label = _label(axes[axis_key])
        buckets.setdefault(label, []).append(bool(record.get("passed", False)))
    return {
        label: (sum(passes) / len(passes) if passes else 0.0)
        for label, passes in buckets.items()
    }


def _axis_labels(views: Sequence[_ModelView], axis_key: str) -> list[str]:
    """Union of axis-value labels across all models, in sorted order.

    Numeric axes (e.g. travelers) sort numerically; everything else sorts
    lexically so the rows are stable and human-scannable.
    """
    labels: set[str] = set()
    for view in views:
        labels.update(_axis_accuracy(view, axis_key))

    def _sort_key(label: str) -> tuple[int, float, str]:
        # (is-non-numeric, numeric-value, label) keeps "1","2","10" ordered.
        try:
            return (0, float(label), label)
        except ValueError:
            return (1, 0.0, label)

    return sorted(labels, key=_sort_key)


# --------------------------------------------------------------------------- #
# Markdown rendering
# --------------------------------------------------------------------------- #


def _headline_table(views: Sequence[_ModelView]) -> list[str]:
    lines = [
        "| model | accuracy | passed/total | latency mean | "
        "latency p50 | latency p95 | total cost | clears bar |",
        "| --- | --: | --: | --: | --: | --: | --: | :-: |",
    ]
    for view in views:
        clears = "yes" if view.clears_bar else "no"
        lines.append(
            f"| {view.model} | {_fmt_pct(view.accuracy)} | "
            f"{view.passed}/{view.scenarios} | {_fmt_secs(view.mean_latency)} | "
            f"{_fmt_secs(view.p50_latency)} | {_fmt_secs(view.p95_latency)} | "
            f"{_fmt_usd(view.total_cost)} | {clears} |"
        )
    return lines


def _per_axis_tables(views: Sequence[_ModelView]) -> list[str]:
    lines: list[str] = []
    for axis_key, title in _AXES:
        lines.append(f"### {title}")
        lines.append("")
        header = "| value | " + " | ".join(v.model for v in views) + " |"
        rule = "| --- | " + " | ".join("--:" for _ in views) + " |"
        lines.append(header)
        lines.append(rule)
        per_model = [_axis_accuracy(v, axis_key) for v in views]
        for label in _axis_labels(views, axis_key):
            cells = []
            for accuracy in per_model:
                # Blank cell when this model never exercised that axis value.
                cells.append(_fmt_pct(accuracy[label]) if label in accuracy else "—")
            lines.append(f"| {label} | " + " | ".join(cells) + " |")
        lines.append("")
    return lines


def _failure_bucket_table(views: Sequence[_ModelView]) -> list[str]:
    all_buckets: set[str] = set()
    for view in views:
        all_buckets.update(view.failure_buckets)
    if not all_buckets:
        return ["No failures across any model."]
    lines = [
        "| bucket | " + " | ".join(v.model for v in views) + " |",
        "| --- | " + " | ".join("--:" for _ in views) + " |",
    ]
    for bucket in sorted(all_buckets):
        cells = [str(v.failure_buckets.get(bucket, 0)) for v in views]
        lines.append(f"| {bucket} | " + " | ".join(cells) + " |")
    return lines


def _scenario_count_guard(views: Sequence[_ModelView]) -> list[str]:
    """Warn (and detail) when the runs do not cover the same scenario count.

    Comparing a 24-scenario probe against a 192-scenario full sweep as if the
    accuracy figures were equivalent would be misleading, so surface it loudly.
    """
    counts = {v.scenarios for v in views}
    if len(counts) <= 1:
        return []
    detail = ", ".join(f"{v.model}={v.scenarios}" for v in views)
    return [
        "> **Warning — uneven scenario coverage.** These runs do not all cover "
        "the same number of scenarios "
        f"({detail}). Accuracy and cost are NOT directly comparable; treat the "
        "smaller run as a partial probe, not a full-corpus verdict.",
        "",
    ]


def _recommendation(views: Sequence[_ModelView]) -> list[str]:
    """Evidence-based v1-engine recommendation for the DUS-21 ADR.

    - No model clears the >=99% bar -> say so; name the algorithmic spike
      (DUS-20) as the fallback.
    - One or more clear it -> among those, pick the cheapest (tie-broken by
      lower mean latency, then higher accuracy) as the suggested v1 engine.
    """
    bar = views[0].accuracy_bar if views else 0.99
    clearing = [v for v in views if v.clears_bar]
    lines: list[str] = []

    if not clearing:
        best = max(views, key=lambda v: (v.accuracy, -v.mean_latency))
        lines.append(
            f"**No model clears the >={_fmt_pct(bar)} route-accuracy bar.** "
            f"The closest is `{best.model}` at {_fmt_pct(best.accuracy)} "
            f"({best.passed}/{best.scenarios}), still short of the gate."
        )
        lines.append("")
        lines.append(
            "Recommendation: do NOT adopt an LLM route engine for v1 on this "
            "evidence. Fall back to the algorithmic engine spike (DUS-20)."
        )
        return lines

    cleared_names = ", ".join(f"`{v.model}`" for v in clearing)
    plural = "s" if len(clearing) > 1 else ""
    lines.append(f"Model{plural} clearing the >={_fmt_pct(bar)} bar: {cleared_names}.")
    lines.append("")

    # Cheapest among the bar-clearers wins; ties broken by latency then accuracy.
    winner = min(
        clearing,
        key=lambda v: (v.total_cost, v.mean_latency, -v.accuracy),
    )
    if len(clearing) == 1:
        lines.append(
            f"Suggested v1 engine: **`{winner.model}`** — the only model that "
            f"clears the bar ({_fmt_pct(winner.accuracy)} accuracy, "
            f"{_fmt_usd(winner.total_cost)} total cost, "
            f"{_fmt_secs(winner.mean_latency)} mean latency)."
        )
    else:
        lines.append(
            f"Suggested v1 engine: **`{winner.model}`** — among the bar-clearers "
            f"it is the cheapest ({_fmt_usd(winner.total_cost)} total cost) at "
            f"{_fmt_pct(winner.accuracy)} accuracy and "
            f"{_fmt_secs(winner.mean_latency)} mean latency."
        )
    return lines


def build_compare_md(runs: list[dict[str, Any]]) -> str:
    """Render the cross-model ``compare.md`` from 2+ loaded ``results.json``.

    ``runs`` are the loaded payloads (dicts) in the order the caller passed
    them; that order is preserved for the table rows/columns. Raises
    ``ValueError`` for fewer than two runs — a comparison needs at least two.
    """
    if len(runs) < 2:
        msg = "build_compare_md needs at least two runs to compare"
        raise ValueError(msg)

    views = [_ModelView.of(payload) for payload in runs]
    bar = views[0].accuracy_bar
    lines: list[str] = []

    lines.append("# Cross-model engine comparison")
    lines.append("")
    lines.append(
        f"Comparing {len(views)} model run(s): "
        + ", ".join(f"`{v.model}`" for v in views)
        + "."
    )
    lines.append("")
    lines.extend(_scenario_count_guard(views))

    lines.append("## Headline")
    lines.append("")
    lines.append(f"Route-accuracy bar: **>={_fmt_pct(bar)}**.")
    lines.append("")
    lines.extend(_headline_table(views))
    lines.append("")

    lines.append("## Per-axis accuracy")
    lines.append("")
    lines.append(
        "Models as columns; a blank (`—`) cell means that model never "
        "exercised that axis value."
    )
    lines.append("")
    lines.extend(_per_axis_tables(views))

    lines.append("## Failure buckets")
    lines.append("")
    lines.extend(_failure_bucket_table(views))
    lines.append("")

    lines.append("## Recommendation")
    lines.append("")
    lines.extend(_recommendation(views))
    lines.append("")

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser (no I/O, so ``--help`` is side-effect free)."""
    parser = argparse.ArgumentParser(
        prog="python -m spikes.route_engine_llm.compare",
        description=(
            "Render a cross-model comparison (compare.md) from 2+ per-run "
            "results.json files produced by the engine spike runner."
        ),
    )
    parser.add_argument(
        "results",
        nargs="+",
        type=Path,
        help="Two or more results.json paths (one per model run).",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        type=Path,
        help=(
            "Output path for compare.md "
            "(default: runs/compare-<timestamp>.md alongside the runs dir)."
        ),
    )
    return parser


def _default_output() -> Path:
    """``runs/compare-<timestamp>.md`` next to the per-run artifacts."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path(__file__).resolve().parent / "runs" / f"compare-{stamp}.md"


def main(argv: list[str] | None = None) -> int:
    """Load the given results.json files, render compare.md, write + report it."""
    args = build_parser().parse_args(argv)

    if len(args.results) < 2:
        print("error: need at least two results.json files to compare", file=sys.stderr)
        return 2

    runs: list[dict[str, Any]] = []
    for path in args.results:
        try:
            runs.append(json.loads(Path(path).read_text(encoding="utf-8")))
        except FileNotFoundError:
            print(f"error: results file not found: {path}", file=sys.stderr)
            return 2
        except json.JSONDecodeError as exc:
            print(f"error: {path} is not valid JSON ({exc})", file=sys.stderr)
            return 2

    markdown = build_compare_md(runs)

    out_path = args.output if args.output is not None else _default_output()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown, encoding="utf-8")
    print(f"compare report: {out_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover - module CLI entrypoint
    raise SystemExit(main())
