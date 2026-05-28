"""The algorithmic engine entrypoint: fold one fragment into a route by rules.

Mirror of :func:`spikes.route_engine_llm.engine.update_route` but with the LLM
swapped for a deterministic rules pipeline. The op list is built by
:func:`spikes.route_engine_algorithmic.rules.build_ops` and applied through the
shared :func:`spikes.route_engine_llm.operations.apply` — so identity
preservation, gap-safe insertion, and stop projection from transits all come
for free, exactly as in the LLM spike.

Rule-pipeline failures (out-of-scope fragment shapes in Slice 1) and applier
errors are both surfaced as a typed :class:`EngineError` so the runner buckets
the scenario as failed and continues the sweep instead of crashing.
"""

# NOTE: imported from the LLM spike's package because the shared types
# (models / operations / corpus / scoring / report) currently live there.
# TODO: extract to a common `engine_core` package when one engine is
# promoted to production (per 003-algorithmic-engine-spike §2.1).
from __future__ import annotations

import time

from spikes.route_engine_algorithmic.rules import RuleNotImplementedError, build_ops
from spikes.route_engine_llm.bedrock_client import Usage
from spikes.route_engine_llm.engine import EngineError, UpdateResult
from spikes.route_engine_llm.models import Fragment, WorkingRoute
from spikes.route_engine_llm.operations import OpApplyError, apply

__all__ = ["EngineError", "UpdateResult", "update_route"]


def update_route(route: WorkingRoute, fragment: Fragment) -> UpdateResult:
    """Fold ``fragment`` into ``route`` deterministically; return the update result.

    Captures wall-clock latency via :func:`time.perf_counter`. Token usage is
    zero — algorithmic has no model spend. Raises :class:`EngineError` wrapping
    either a :class:`RuleNotImplementedError` (out-of-scope fragment shape) or
    an :class:`OpApplyError` (dangling/conflicting op), so a single scenario's
    failure never aborts the run.
    """
    started = time.perf_counter()

    try:
        ops = build_ops(route, fragment)
    except RuleNotImplementedError as exc:
        msg = f"algorithmic rules cannot handle this fragment yet: {exc}"
        raise EngineError(msg, cause=exc) from exc

    try:
        apply(route, ops)
    except OpApplyError as exc:
        msg = f"algorithmic rules produced a dangling or conflicting operation: {exc}"
        raise EngineError(msg, cause=exc) from exc

    latency = time.perf_counter() - started
    return UpdateResult(
        route=route,
        ops=ops,
        usage=Usage(input_tokens=0, output_tokens=0),
        latency_seconds=latency,
    )
