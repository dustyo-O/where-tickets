"""CLI runner: replay the corpus through the engine against one Bedrock model.

    python -m spikes.route_engine_llm.run --model {opus|sonnet|haiku} \
        [--scenario NNN-... | --shape {straight|circle|star} | --limit N]

For each selected scenario (technical-considerations §2.4): start from an empty
:class:`WorkingRoute`, feed its fragments IN CORPUS ORDER, call
:func:`update_route` per fragment with a live Bedrock client, snapshot the route
after each fragment, then :func:`score_scenario` over the snapshots. Per-call
latency + token usage are collected; cost is estimated via
:mod:`spikes.route_engine_llm.pricing`. Artifacts land in a timestamped
``runs/<timestamp>-<model>/`` directory (never overwritten): ``results.json`` +
``report.md``.

CI-safety: ``anthropic`` is imported only lazily inside ``make_client`` (and
boto credential resolution only happens on the live call), so this module
imports and ``--help`` parses with neither the ``spike`` group installed nor AWS
credentials configured. A missing dependency or unconfigured AWS surfaces as a
clear, actionable message — not a stack trace. A single engine error on one
scenario marks THAT scenario failed and the sweep continues.
"""

from __future__ import annotations

import argparse
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from spikes.route_engine_llm.bedrock_client import Usage, make_client, resolve_model_id
from spikes.route_engine_llm.corpus import corpus_root, load_scenario
from spikes.route_engine_llm.engine import EngineError, update_route
from spikes.route_engine_llm.models import WorkingRoute
from spikes.route_engine_llm.pricing import (
    PRICING_AS_OF,
    PRICING_SOURCE,
    cost_usd,
    resolve_price,
)
from spikes.route_engine_llm.report import (
    RunMeta,
    ScenarioResult,
    build_report_md,
    build_results_json,
)
from spikes.route_engine_llm.scoring import score_scenario

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

    from spikes.route_engine_llm.bedrock_client import BedrockEngineClient
    from spikes.route_engine_llm.corpus import Scenario
    from spikes.route_engine_llm.pricing import ModelPrice

__all__ = [
    "discover_scenarios",
    "run_scenario",
    "build_parser",
    "main",
]

_MODELS = ("opus", "sonnet", "haiku")
_SHAPES = ("straight", "circle", "star")


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

    Filters compose: ``scenario`` pins exactly one; ``shape`` keeps only that
    shape; ``limit`` truncates after sorting. An empty selection raises
    :class:`RunnerError` so the runner never silently does nothing.
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


def _sum_usage(usages: Sequence[Usage]) -> Usage:
    """Sum token usage across a scenario's per-fragment calls."""
    return Usage(
        input_tokens=sum(u.input_tokens for u in usages),
        output_tokens=sum(u.output_tokens for u in usages),
        cache_creation_input_tokens=sum(u.cache_creation_input_tokens for u in usages),
        cache_read_input_tokens=sum(u.cache_read_input_tokens for u in usages),
    )


def run_scenario(
    scenario: Scenario,
    client: BedrockEngineClient,
    price: ModelPrice,
) -> ScenarioResult:
    """Replay one scenario fragment-by-fragment, score it, and price it.

    A single :class:`EngineError` (bad/dangling op) marks the scenario failed
    with the partial latency/usage gathered so far, and the caller continues the
    sweep — it never aborts the whole run.
    """
    route = WorkingRoute()
    snapshots: list[WorkingRoute] = []
    latencies: list[float] = []
    usages: list[Usage] = []

    for fragment in scenario.fragments:
        try:
            result = update_route(route, fragment, client)
        except EngineError as exc:
            return ScenarioResult(
                name=scenario.name,
                passed=False,
                fragment_latencies=latencies,
                usage=_sum_usage(usages),
                cost_usd=sum(cost_usd(u, price) for u in usages),
                error=str(exc),
            )
        latencies.append(result.latency_seconds)
        usages.append(result.usage)
        # Snapshot the route's state AFTER this fragment, decoupled from later
        # mutations, so the identity/ordering checks see each step faithfully.
        snapshots.append(deepcopy(route))

    score = score_scenario(snapshots, scenario.expected)
    summed = _sum_usage(usages)
    return ScenarioResult(
        name=scenario.name,
        passed=score.passed,
        failure_category=None if score.passed else str(score.category),
        fragment_latencies=latencies,
        usage=summed,
        cost_usd=sum(cost_usd(u, price) for u in usages),
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser (no I/O, so ``--help`` is side-effect free)."""
    parser = argparse.ArgumentParser(
        prog="python -m spikes.route_engine_llm.run",
        description=(
            "Replay the corpus through the LLM route engine against one "
            "Bedrock Claude model and write a timestamped run report."
        ),
    )
    parser.add_argument(
        "--model",
        required=True,
        choices=_MODELS,
        help="Bedrock Claude model alias to evaluate.",
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
    parser.add_argument(
        "--region",
        default=None,
        help="AWS region override (defaults to AWS_REGION).",
    )
    return parser


def _run_dir(model: str, started: datetime) -> Path:
    """Compute a fresh, never-overwritten ``runs/<timestamp>-<model>/`` path."""
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    base = Path(__file__).resolve().parent / "runs" / f"{stamp}-{model}"
    # Distinguish sub-second re-runs so an existing dir is never clobbered.
    candidate = base
    suffix = 1
    while candidate.exists():
        candidate = base.with_name(f"{base.name}-{suffix}")
        suffix += 1
    return candidate


def main(argv: list[str] | None = None) -> int:
    """Entry point: parse args, run the sweep, write artifacts, print summary.

    Returns a process exit code. Live-only failures (missing ``anthropic``,
    unconfigured AWS, bad filters) are reported as a single actionable line.
    """
    args = build_parser().parse_args(argv)

    try:
        names = discover_scenarios(
            scenario=args.scenario, shape=args.shape, limit=args.limit
        )
    except (RunnerError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    price = resolve_price(args.model)

    # Build the live client LAST, after all offline validation, so argument and
    # filter errors never require AWS or the `spike` group. A missing dependency
    # or unconfigured AWS becomes a clear message, not a traceback.
    try:
        client = make_client(args.model, region=args.region)
    except ImportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001 - surface any boto/cred error cleanly
        print(
            "error: could not initialize the Bedrock client "
            f"({type(exc).__name__}: {exc}). Check AWS credentials and region "
            "(AWS_REGION) and that Bedrock model access is enabled.",
            file=sys.stderr,
        )
        return 3

    started = datetime.now(UTC)
    results: list[ScenarioResult] = []
    for name in names:
        scenario = load_scenario(name)
        results.append(run_scenario(scenario, client, price))

    meta = RunMeta(
        model=args.model,
        model_id=resolve_model_id(args.model),
        started_at=started,
        pricing_as_of=PRICING_AS_OF,
        pricing_source=PRICING_SOURCE,
    )

    out_dir = _run_dir(args.model, started)
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
        f"{args.model}: {passed}/{total} passed ({accuracy * 100:.1f}%), "
        f"est. cost ${total_cost:.4f}"
    )
    print(f"artifacts: {out_dir}")
    return 0


if __name__ == "__main__":  # pragma: no cover - module CLI entrypoint
    raise SystemExit(main())
