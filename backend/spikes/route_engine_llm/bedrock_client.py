"""Thin, injectable wrapper over Anthropic's ``AnthropicBedrock`` client.

The engine depends on the small :class:`BedrockEngineClient` protocol, not on
``anthropic`` directly, so:

- offline / stubbed tests can pass a fake client and never import ``anthropic``;
- pyright type-checks cleanly even when the optional ``spike`` group (which
  carries ``anthropic[bedrock]``) is not installed.

``anthropic`` is imported LAZILY inside :func:`make_client` — never at module
top level — so importing this module and running stubbed tests works without the
package present.

Model selection: a ``{opus|sonnet|haiku}`` alias maps to a Bedrock
inference-profile id. Defaults target the EU cross-region inference profiles for
the current Claude models (the project runs in eu-north-1); every id is
overridable via environment variables so we never hardcode an id that might be
wrong for a given account/region:

    SPIKE_BEDROCK_MODEL_OPUS    (default: eu.anthropic.claude-opus-4-6-v1)
    SPIKE_BEDROCK_MODEL_SONNET  (default: eu.anthropic.claude-sonnet-4-6)
    SPIKE_BEDROCK_MODEL_HAIKU   (default: eu.anthropic.claude-haiku-4-5-20251001-v1:0)

Opus default is 4.6 (4.7 was not enabled in the target account); override
``SPIKE_BEDROCK_MODEL_OPUS`` to use a different one. Region comes from
``AWS_REGION`` (the AnthropicBedrock client's own default chain otherwise
applies).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    # `anthropic` ships only in the optional `spike` group; guard the type
    # import so pyright stays clean when the package is absent.
    from anthropic import AnthropicBedrock  # pyright: ignore[reportMissingImports]

__all__ = [
    "ModelAlias",
    "MODEL_PROFILE_DEFAULTS",
    "Usage",
    "ToolUseResult",
    "BedrockEngineClient",
    "AnthropicBedrockEngineClient",
    "resolve_model_id",
    "make_client",
]

# The selectable model aliases.
type ModelAlias = str  # one of: "opus" | "sonnet" | "haiku"

# Default Bedrock inference-profile IDs per alias. EU cross-region profiles for
# the current Claude generation (project runs in eu-north-1); override per-alias
# via the env vars below.
MODEL_PROFILE_DEFAULTS: dict[str, str] = {
    "opus": "eu.anthropic.claude-opus-4-6-v1",
    "sonnet": "eu.anthropic.claude-sonnet-4-6",
    "haiku": "eu.anthropic.claude-haiku-4-5-20251001-v1:0",
}

_ENV_OVERRIDE: dict[str, str] = {
    "opus": "SPIKE_BEDROCK_MODEL_OPUS",
    "sonnet": "SPIKE_BEDROCK_MODEL_SONNET",
    "haiku": "SPIKE_BEDROCK_MODEL_HAIKU",
}

# Deterministic decoding for the spike (minimizes non-determinism in scoring).
_TEMPERATURE = 0
# Generous cap: an operation list for a single fragment is small, but tool-use
# JSON plus any thinking should never be truncated.
_MAX_TOKENS = 4096
# SDK-level bounded exponential backoff on Bedrock throttling (429 / 5xx).
_MAX_RETRIES = 4


# --------------------------------------------------------------------------- #
# Result + usage records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Usage:
    """Token usage for one Bedrock call (cache fields are 0 when not reported)."""

    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ToolUseResult:
    """The parsed tool input plus the call's usage and wall-clock latency."""

    tool_input: dict[str, Any]
    usage: Usage
    latency_seconds: float


# --------------------------------------------------------------------------- #
# Injectable client interface
# --------------------------------------------------------------------------- #


@runtime_checkable
class BedrockEngineClient(Protocol):
    """Minimal interface the engine depends on (DI seam for tests).

    A real implementation calls Bedrock; the contract test passes a fake.
    """

    def complete(
        self,
        *,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any],
    ) -> ToolUseResult:
        """Run one forced-tool-use turn and return the parsed tool input."""
        ...


class ToolUseNotReturnedError(RuntimeError):
    """Raised when a forced tool-use call returns no matching tool_use block."""


# --------------------------------------------------------------------------- #
# Model id resolution
# --------------------------------------------------------------------------- #


def resolve_model_id(alias: ModelAlias) -> str:
    """Map a ``{opus|sonnet|haiku}`` alias to its Bedrock inference-profile id.

    An ``SPIKE_BEDROCK_MODEL_<ALIAS>`` env var overrides the default.
    """
    key = alias.lower()
    if key not in MODEL_PROFILE_DEFAULTS:
        valid = ", ".join(sorted(MODEL_PROFILE_DEFAULTS))
        msg = f"unknown model alias {alias!r}; expected one of: {valid}"
        raise ValueError(msg)
    env_var = _ENV_OVERRIDE[key]
    return os.environ.get(env_var) or MODEL_PROFILE_DEFAULTS[key]


# --------------------------------------------------------------------------- #
# Real Bedrock-backed client
# --------------------------------------------------------------------------- #


class AnthropicBedrockEngineClient:
    """A :class:`BedrockEngineClient` backed by ``AnthropicBedrock``.

    Holds a resolved model id and the underlying SDK client; ``complete`` issues
    a single ``temperature=0`` forced-tool-use call, capturing usage + latency.
    """

    def __init__(self, raw_client: AnthropicBedrock, model_id: str) -> None:
        self._client = raw_client
        self._model_id = model_id

    @property
    def model_id(self) -> str:
        """The resolved Bedrock inference-profile id this client targets."""
        return self._model_id

    def complete(
        self,
        *,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any],
    ) -> ToolUseResult:
        """Issue one forced-tool-use call; return parsed input + usage/latency."""
        started = time.perf_counter()
        response = self._client.messages.create(
            model=self._model_id,
            max_tokens=_MAX_TOKENS,
            temperature=_TEMPERATURE,
            system=system,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
        )
        latency = time.perf_counter() - started

        tool_input = _extract_tool_input(
            response, expected_name=tool_choice.get("name")
        )
        usage = _extract_usage(response)
        return ToolUseResult(
            tool_input=tool_input,
            usage=usage,
            latency_seconds=latency,
        )


def _extract_tool_input(response: Any, *, expected_name: str | None) -> dict[str, Any]:
    """Pull the first matching ``tool_use`` block's input off a response."""
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) != "tool_use":
            continue
        if expected_name is not None and getattr(block, "name", None) != expected_name:
            continue
        block_input = getattr(block, "input", None)
        if isinstance(block_input, dict):
            return block_input
    msg = "forced tool-use call returned no tool_use block" + (
        f" named {expected_name!r}" if expected_name else ""
    )
    raise ToolUseNotReturnedError(msg)


def _extract_usage(response: Any) -> Usage:
    """Build a :class:`Usage` from a response's ``usage`` (defaults to 0)."""
    raw = getattr(response, "usage", None)
    return Usage(
        input_tokens=int(getattr(raw, "input_tokens", 0) or 0),
        output_tokens=int(getattr(raw, "output_tokens", 0) or 0),
        cache_creation_input_tokens=int(
            getattr(raw, "cache_creation_input_tokens", 0) or 0
        ),
        cache_read_input_tokens=int(getattr(raw, "cache_read_input_tokens", 0) or 0),
    )


# --------------------------------------------------------------------------- #
# Factory (lazy import of `anthropic`)
# --------------------------------------------------------------------------- #


def make_client(
    alias: ModelAlias,
    *,
    region: str | None = None,
) -> AnthropicBedrockEngineClient:
    """Build a Bedrock-backed engine client for ``alias``.

    Imports ``anthropic`` LAZILY so this module and the offline tests work
    without the optional ``spike`` dependency group installed. Region defaults
    to ``AWS_REGION``; the SDK applies its own default chain when unset.
    """
    try:
        # Lazy + optional: `anthropic` is only present with the `spike` group.
        from anthropic import AnthropicBedrock  # noqa: PLC0415  # pyright: ignore[reportMissingImports]
    except ImportError as exc:  # pragma: no cover - exercised only without the dep
        msg = (
            "anthropic[bedrock] is required for live Bedrock calls; install the "
            "'spike' dependency group: `uv sync --group spike`"
        )
        raise ImportError(msg) from exc

    model_id = resolve_model_id(alias)
    aws_region = region or os.environ.get("AWS_REGION")
    raw_client = AnthropicBedrock(aws_region=aws_region, max_retries=_MAX_RETRIES)
    return AnthropicBedrockEngineClient(raw_client, model_id)
