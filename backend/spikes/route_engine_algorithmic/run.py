"""CLI runner: replay the corpus through the algorithmic engine.

    python -m spikes.route_engine_algorithmic.run \
        [--scenario NNN-... | --shape {straight|circle|star} | --limit N]

Mirrors :mod:`spikes.route_engine_llm.run` closely — same scenario discovery,
same replay loop, same artifact layout — but with the LLM call swapped for the
deterministic :func:`spikes.route_engine_algorithmic.engine.update_route`. No
``--model``/``--region``: the engine alias is hardcoded ``"algorithmic"``. No
``anthropic``, no AWS credentials, no spike dependency group — CI-safe by
construction. A scenario-level :class:`EngineError` (e.g. an out-of-Slice-1
fragment shape) buckets that scenario as failed and the sweep continues to the
next one.
"""

# NOTE: imported from the LLM spike's package because the shared types
# (models / operations / corpus / scoring / report) currently live there.
# TODO: extract to a common `engine_core` package when one engine is
# promoted to production (per 003-algorithmic-engine-spike §2.1).
from __future__ import annotations

import argparse
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from spikes.route_engine_algorithmic.engine import EngineError, update_route
from spikes.route_engine_llm.bedrock_client import Usage
from spikes.route_engine_llm.corpus import corpus_root, load_scenario
from spikes.route_engine_llm.models import WorkingRoute
from spikes.route_engine_llm.report import (
    RunMeta,
    ScenarioResult,
    build_report_md,
    build_results_json,
)
from spikes.route_engine_llm.scoring import score_scenario

if TYPE_CHECKING:  # pragma: no cover - typing only
    from spikes.route_engine_llm.corpus import Scenario

__all__ = [
    "discover_scenarios",
    "run_scenario",
    "build_parser",
    "main",
]

_SHAPES = ("straight", "circle", "star")

# Engine alias is fixed: this runner is the algorithmic one. The LLM runner
# accepts opus/sonnet/haiku; we reuse the same `results.json` schema so the
# cross-model compare can ingest us as another column.
_ENGINE_ALIAS = "algorithmic"

# Pricing metadata is required by the shared `RunMeta` but is N/A for the
# algorithmic engine — no tokens, no spend. Document the carve-out inline.
_PRICING_AS_OF = "n/a"
_PRICING_SOURCE = "algorithmic engine — no token spend"


class RunnerError(Exception):
    """A user-actionable runner failure (printed as a message, not a traceback)."""


# --------------------------------------------------------------------------- #
# Scenario discovery + filtering
# --------------------------------------------------------------------------- #


def discover_scenarios(
    *,
    scenario: str | None = None,
    shape: str | None = None,
    limit: int | None = None,
) -> list[str]:
    """Return the selected scenario names in corpus (lexical) order.

    Same semantics as the LLM runner: filters compose; an empty selection
    raises :class:`RunnerError` so the runner never silently does nothing.
    """
    scenarios_dir = corpus_root() / "scenarios"
    names = sorted(p.name for p in scenarios_dir.iterdir() if p.is_dir())

    if scenario is not None:
        if scenario not in names:
            msg = f"scenario {scenario!r} not found under {scenarios_dir}"
            raise RunnerError(msg)
        names = [scenario]

    if shape is not None:
        names = [n for n in names if n.split("-", 2)[1:2] == [shape]]

    if limit is not None:
        names = names[:limit]

    if not names:
        raise RunnerError("no scenarios matched the given filters")
    return names


# --------------------------------------------------------------------------- #
# Per-scenario replay
# --------------------------------------------------------------------------- #


def run_scenario(scenario: Scenario) -> ScenarioResult:
    """Replay one scenario fragment-by-fragment, score it; cost is always $0.

    A single :class:`EngineError` (e.g. a Slice-1 out-of-scope fragment) marks
    the scenario failed with the partial latencies gathered so far and returns
    — the caller continues the sweep, identical to the LLM runner's contract.
    """
    route = WorkingRoute()
    snapshots: list[WorkingRoute] = []
    latencies: list[float] = []

    for fragment in scenario.fragments:
        try:
            result = update_route(route, fragment)
        except EngineError as exc:
            return ScenarioResult(
                name=scenario.name,
                passed=False,
                failure_category=None,  # bucketed as `engine_error` by the report
                fragment_latencies=latencies,
                usage=Usage(input_tokens=0, output_tokens=0),
                cost_usd=0.0,
                error=str(exc),
            )
        latencies.append(result.latency_seconds)
        # Snapshot the route's state AFTER this fragment, decoupled from later
        # mutations, so the identity/ordering checks see each step faithfully.
        snapshots.append(deepcopy(route))

    score = score_scenario(snapshots, scenario.expected)
    return ScenarioResult(
        name=scenario.name,
        passed=score.passed,
        failure_category=None if score.passed else str(score.category),
        fragment_latencies=latencies,
        usage=Usage(input_tokens=0, output_tokens=0),
        cost_usd=0.0,
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser (no I/O, so ``--help`` is side-effect free)."""
    parser = argparse.ArgumentParser(
        prog="python -m spikes.route_engine_algorithmic.run",
        description=(
            "Replay the corpus through the deterministic algorithmic route "
            "engine and write a timestamped run report (no AWS, no tokens, no "
            "spend)."
        ),
    )
    parser.add_argument(
        "--scenario",
        default=None,
        help="Run exactly one scenario by name (e.g. 000-straight-1p-forward).",
    )
    parser.add_argument(
        "--shape",
        default=None,
        choices=_SHAPES,
        help="Restrict to one route shape.",
    )
    parser.add_argument(
        "--limit",
        default=None,
        type=int,
        help="Run at most N scenarios (after sorting/filtering).",
    )
    return parser


def _run_dir(started: datetime) -> Path:
    """Compute a fresh, never-overwritten ``runs/<timestamp>-algorithmic/`` path."""
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    base = Path(__file__).resolve().parent / "runs" / f"{stamp}-{_ENGINE_ALIAS}"
    # Distinguish sub-second re-runs so an existing dir is never clobbered.
    candidate = base
    suffix = 1
    while candidate.exists():
        candidate = base.with_name(f"{base.name}-{suffix}")
        suffix += 1
    return candidate


def main(argv: list[str] | None = None) -> int:
    """Entry point: parse args, run the sweep, write artifacts, print summary.

    Bad filters return exit code 2 with a single actionable line, matching the
    LLM runner's convention.
    """
    args = build_parser().parse_args(argv)

    try:
        names = discover_scenarios(
            scenario=args.scenario, shape=args.shape, limit=args.limit
        )
    except (RunnerError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    started = datetime.now(UTC)
    results: list[ScenarioResult] = []
    for name in names:
        scenario = load_scenario(name)
        results.append(run_scenario(scenario))

    meta = RunMeta(
        model=_ENGINE_ALIAS,
        model_id=_ENGINE_ALIAS,
        started_at=started,
        pricing_as_of=_PRICING_AS_OF,
        pricing_source=_PRICING_SOURCE,
    )

    out_dir = _run_dir(started)
    out_dir.mkdir(parents=True, exist_ok=False)
    (out_dir / "results.json").write_text(
        build_results_json(meta, results), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(build_report_md(meta, results), encoding="utf-8")

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    total_cost = sum(r.cost_usd for r in results)
    accuracy = passed / total if total else 0.0
    print(
        f"{_ENGINE_ALIAS}: {passed}/{total} passed ({accuracy * 100:.1f}%), "
        f"est. cost ${total_cost:.4f}"
    )
    print(f"artifacts: {out_dir}")
    return 0


if __name__ == "__main__":  # pragma: no cover - module CLI entrypoint
    raise SystemExit(main())
