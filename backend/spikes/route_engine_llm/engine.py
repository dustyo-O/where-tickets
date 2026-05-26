"""The engine entrypoint: fold one fragment into a route via the LLM.

``update_route(route, fragment, client)`` is the single conceptual entrypoint
the runner and the eventual production engine share:

    given an existing route + one new document fragment, return an updated route.

It renders the prompt, calls the injectable Bedrock client with forced tool-use,
validates + parses the returned tool input into the Slice-1 op models, and
applies them with the deterministic applier. Our applier owns identity, so the
append/identity hard gate holds by construction.

Invalid or dangling operations (a malformed op payload, or an op referencing an
unknown stop id / conflicting enrichment) are surfaced as a typed
:class:`EngineError` rather than crashing — the runner buckets these as model
errors.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import TypeAdapter, ValidationError

from spikes.route_engine_llm.bedrock_client import BedrockEngineClient, Usage
from spikes.route_engine_llm.models import Fragment, WorkingRoute
from spikes.route_engine_llm.operations import Op, OpApplyError, apply
from spikes.route_engine_llm.prompts import (
    build_system_blocks,
    build_tool,
    build_tool_choice,
)

__all__ = [
    "EngineError",
    "UpdateResult",
    "update_route",
    "render_user_message",
]

# Reusable adapter that validates the model's `operations` list into op models.
_OPS_ADAPTER: TypeAdapter[list[Op]] = TypeAdapter(list[Op])


class EngineError(Exception):
    """A typed failure from an engine step (bad op payload or dangling op).

    Carries the originating cause so the runner can bucket failures without the
    process crashing.
    """

    def __init__(self, message: str, *, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.__cause__ = cause


@dataclass(frozen=True, slots=True)
class UpdateResult:
    """Outcome of one ``update_route`` call.

    ``route`` is the same (mutated) route instance passed in. ``ops`` is the
    parsed op list that was applied. ``usage`` / ``latency_seconds`` come from
    the Bedrock call so the runner can aggregate cost + latency.
    """

    route: WorkingRoute
    ops: list[Op]
    usage: Usage
    latency_seconds: float


def render_user_message(route: WorkingRoute, fragment: Fragment) -> dict[str, object]:
    """Build the per-call user message: current route + the new fragment.

    This is the only volatile content per call; it goes AFTER the cached system
    prompt + tool schema in render order, preserving the cache prefix.
    """
    route_json = route.model_dump_json(by_alias=True)
    fragment_json = fragment.model_dump_json(by_alias=True)
    text = (
        "CURRENT ROUTE (engine-owned ids; authoritative — append/enrich only):\n"
        f"{route_json}\n\n"
        "NEW FRAGMENT to fold in:\n"
        f"{fragment_json}\n\n"
        "Return the operations that fold this fragment into the route."
    )
    return {"role": "user", "content": text}


def update_route(
    route: WorkingRoute,
    fragment: Fragment,
    client: BedrockEngineClient,
) -> UpdateResult:
    """Fold ``fragment`` into ``route`` via ``client``; return the update result.

    ``client`` is injected (dependency injection) so the contract test can pass
    a fake. Raises :class:`EngineError` on an invalid or dangling op set.
    """
    result = client.complete(
        system=build_system_blocks(),
        messages=[render_user_message(route, fragment)],
        tools=[build_tool()],
        tool_choice=build_tool_choice(),
    )

    raw_ops = result.tool_input.get("operations")
    if raw_ops is None:
        msg = "tool input missing required 'operations' field"
        raise EngineError(msg)

    try:
        ops = _OPS_ADAPTER.validate_python(raw_ops)
    except ValidationError as exc:
        msg = f"model returned an invalid operations payload: {exc}"
        raise EngineError(msg, cause=exc) from exc

    try:
        apply(route, ops)
    except OpApplyError as exc:
        msg = f"model returned a dangling or conflicting operation: {exc}"
        raise EngineError(msg, cause=exc) from exc

    return UpdateResult(
        route=route,
        ops=ops,
        usage=result.usage,
        latency_seconds=result.latency_seconds,
    )
