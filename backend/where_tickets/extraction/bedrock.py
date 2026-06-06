"""Thin, injectable wrapper over Anthropic's ``AnthropicBedrock`` client.

The PDF extractor depends on the small :class:`BedrockExtractionClient`
protocol, not on ``anthropic`` directly, so:

- offline / stubbed tests can pass a fake client and never import ``anthropic``;
- pyright type-checks cleanly even when the optional ``extraction`` group
  (which carries ``anthropic[bedrock]``) is not installed.

``anthropic`` is imported LAZILY inside :func:`make_client` — never at module
top level — so importing this module and running the wrapper unit tests works
without the package present. The factory raises a clear :class:`ImportError`
when the package is missing, pointing the caller at the right install hint.

Two call shapes, one wrapper:

- :meth:`AnthropicBedrockExtractionClient.complete_text` — tool-use call
  (PATH A first Haiku-on-text pass, PATH B vision-leg Haiku pass, PATH C
  Sonnet text fallback). Returns the parsed :class:`ToolUseResult` so the
  orchestrator can branch on which tool fired (e.g. PATH A offers two tools
  and the orchestrator routes based on ``tool_name``).
- :meth:`AnthropicBedrockExtractionClient.complete_vision` — plain-text
  multi-image call (Sonnet OCRs page JPEGs into raw text for the vision leg).

Model selection: a ``{haiku|sonnet}`` alias maps to a Bedrock inference-profile
id. Defaults target the EU cross-region inference profiles for the current
Claude generation (the project runs in eu-north-1); every id is overridable
via environment variables so we never hardcode an id that might be wrong for a
given account/region:

    WT_BEDROCK_MODEL_HAIKU   (default: eu.anthropic.claude-haiku-4-5-20251001-v1:0)
    WT_BEDROCK_MODEL_SONNET  (default: eu.anthropic.claude-sonnet-4-6)

Region comes from ``AWS_REGION`` (the AnthropicBedrock client's own default
chain otherwise applies).

Prompt caching: per Anthropic's prompt-caching contract, cache breakpoints are
marked with ``cache_control: {"type": "ephemeral"}`` blocks. This wrapper
applies the marker to the system prompt block it builds, and PASSES THROUGH
the ``tools`` list verbatim — the caller is responsible for marking the
tool(s) it wants cached. The Slice 3 prompts module already stamps
``cache_control`` on both tool entries (``TOOL_EMIT_EXTRACTED_FIELDS`` and
``TOOL_REPORT_NO_USEFUL_INFORMATION``), so whichever subset / order the
orchestrator passes in, the last entry already carries the marker. Per-call
user content (PDF text, image bytes) goes AFTER the cache boundary and is
never marked.
"""

from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    # `anthropic` ships only in the optional `extraction` group; guard the type
    # import so pyright stays clean when the package is absent.
    from anthropic import AnthropicBedrock  # pyright: ignore[reportMissingImports]

__all__ = [
    "MODEL_PROFILE_DEFAULTS",
    "AnthropicBedrockExtractionClient",
    "BedrockExtractionClient",
    "ModelAlias",
    "ToolUseNotReturnedError",
    "ToolUseResult",
    "Usage",
    "make_client",
    "resolve_model_id",
]

# The selectable model aliases for the extractor.
type ModelAlias = str  # one of: "haiku" | "sonnet"

# Default Bedrock inference-profile IDs per alias. EU cross-region profiles for
# the current Claude generation (project runs in eu-north-1); override per-alias
# via the env vars below.
MODEL_PROFILE_DEFAULTS: dict[str, str] = {
    "haiku": "eu.anthropic.claude-haiku-4-5-20251001-v1:0",
    "sonnet": "eu.anthropic.claude-sonnet-4-6",
}

_ENV_OVERRIDE: dict[str, str] = {
    "haiku": "WT_BEDROCK_MODEL_HAIKU",
    "sonnet": "WT_BEDROCK_MODEL_SONNET",
}

# Deterministic decoding for extraction (cache hits + reproducibility).
_TEMPERATURE = 0
# Text-path tool-use JSON output: generous cap so an emit_extracted_fields
# payload with many legs / travelers / venues is never truncated.
_MAX_TOKENS_TEXT = 4096
# Vision raw-text output: bigger cap because Sonnet rewrites every visible
# character on every page in reading order.
_MAX_TOKENS_VISION = 8192
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
    """The parsed tool input plus the call's usage and wall-clock latency.

    ``tool_name`` records WHICH tool the model picked. PATH A offers two
    tools (``emit_extracted_fields`` vs ``report_no_useful_information``) and
    the orchestrator branches on this; PATHs B and C force a single tool, so
    the name is redundant but still populated for log/trace fidelity.
    """

    tool_name: str
    tool_input: dict[str, Any]
    usage: Usage
    latency_seconds: float


class ToolUseNotReturnedError(RuntimeError):
    """Raised when a tool-use call returns no ``tool_use`` block at all.

    This is a hard failure of the model contract — every text-path call is
    expected to terminate by calling one of the offered tools.
    """


# --------------------------------------------------------------------------- #
# Injectable client interface
# --------------------------------------------------------------------------- #


@runtime_checkable
class BedrockExtractionClient(Protocol):
    """Minimal interface the PDF extractor depends on (DI seam for tests).

    A real implementation calls Bedrock; the extractor unit tests pass a
    :class:`FakeBedrockExtractionClient` that returns scripted responses.

    Both methods take ``model_alias`` per-call (rather than baking a model id
    into the client) because a single extractor run may issue calls against
    both Haiku (text path) and Sonnet (vision + text fallback) in turn.
    """

    def complete_text(
        self,
        *,
        model_alias: str,
        system: str,
        user_text: str,
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any],
    ) -> ToolUseResult:
        """Run a tool-use call on a text-only user message.

        ``system`` is wrapped in a cached content block by the implementation.
        ``tools`` is passed through verbatim — the caller is responsible for
        marking the cache breakpoint on the last tool entry. ``tool_choice``
        is the standard Anthropic tool-choice object (e.g. ``{"type": "any"}``
        for PATH A, ``{"type": "tool", "name": "emit_extracted_fields"}`` for
        the forced-tool paths).
        """
        ...

    def complete_vision(
        self,
        *,
        model_alias: str,
        system: str,
        images: list[bytes],
        prompt: str,
    ) -> str:
        """Run a plain-text vision call on a sequence of JPEG page images.

        Each entry in ``images`` is base64-encoded into an Anthropic image
        block in the order given (the orchestrator renders the PDF pages in
        reading order). Returns the concatenated plain-text response.
        """
        ...


# --------------------------------------------------------------------------- #
# Model id resolution
# --------------------------------------------------------------------------- #


def resolve_model_id(alias: ModelAlias) -> str:
    """Map a ``{haiku|sonnet}`` alias to its Bedrock inference-profile id.

    A ``WT_BEDROCK_MODEL_<ALIAS>`` env var overrides the default. The alias
    is matched case-insensitively. An unknown alias raises ``ValueError`` with
    the offending value in the message.
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


class AnthropicBedrockExtractionClient:
    """A :class:`BedrockExtractionClient` backed by ``AnthropicBedrock``.

    Holds a single underlying SDK client; the model id is resolved per-call
    from the ``model_alias`` argument so one wrapper instance can drive both
    Haiku and Sonnet within a single extractor run.
    """

    def __init__(self, raw_client: AnthropicBedrock) -> None:
        self._client = raw_client

    def complete_text(
        self,
        *,
        model_alias: str,
        system: str,
        user_text: str,
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any],
    ) -> ToolUseResult:
        """Issue one tool-use call; return parsed input + usage/latency."""
        model = resolve_model_id(model_alias)
        system_blocks = _system_blocks(system)
        messages = [{"role": "user", "content": user_text}]

        started = time.perf_counter()
        response = self._client.messages.create(
            model=model,
            max_tokens=_MAX_TOKENS_TEXT,
            temperature=_TEMPERATURE,
            system=system_blocks,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
        )
        latency = time.perf_counter() - started

        tool_name, tool_input = _extract_tool_use(response)
        usage = _extract_usage(response)
        return ToolUseResult(
            tool_name=tool_name,
            tool_input=tool_input,
            usage=usage,
            latency_seconds=latency,
        )

    def complete_vision(
        self,
        *,
        model_alias: str,
        system: str,
        images: list[bytes],
        prompt: str,
    ) -> str:
        """Issue one multi-image plain-text call; return concatenated text."""
        model = resolve_model_id(model_alias)
        system_blocks = _system_blocks(system)
        content: list[dict[str, Any]] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.b64encode(jpeg).decode("ascii"),
                },
            }
            for jpeg in images
        ]
        content.append({"type": "text", "text": prompt})
        messages = [{"role": "user", "content": content}]

        response = self._client.messages.create(
            model=model,
            max_tokens=_MAX_TOKENS_VISION,
            temperature=_TEMPERATURE,
            system=system_blocks,
            messages=messages,
        )
        return _extract_text(response)


def _system_blocks(system: str) -> list[dict[str, Any]]:
    """Wrap ``system`` in a single cached content block.

    The system prompt is static across calls (see Slice 3 prompts), so marking
    it with an ephemeral cache breakpoint is the documented pattern.
    """
    return [
        {
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _extract_tool_use(response: Any) -> tuple[str, dict[str, Any]]:
    """Pull the first ``tool_use`` block's (name, input) off a response."""
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) != "tool_use":
            continue
        block_input = getattr(block, "input", None)
        block_name = getattr(block, "name", None)
        if isinstance(block_input, dict) and isinstance(block_name, str):
            return block_name, block_input
    msg = "tool-use call returned no tool_use block"
    raise ToolUseNotReturnedError(msg)


def _extract_text(response: Any) -> str:
    """Concatenate every ``text`` block on a vision response into one string."""
    chunks: list[str] = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) != "text":
            continue
        text = getattr(block, "text", None)
        if isinstance(text, str):
            chunks.append(text)
    return "".join(chunks)


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


def make_client(*, region: str | None = None) -> AnthropicBedrockExtractionClient:
    """Build a Bedrock-backed extraction client.

    Imports ``anthropic`` LAZILY so this module and the wrapper unit tests
    work without the optional ``extraction`` dependency group installed.
    Region defaults to ``AWS_REGION``; the SDK applies its own default chain
    when unset.

    Raises :class:`ImportError` with a clear install hint when ``anthropic``
    is not importable.
    """
    try:
        # Lazy + optional: `anthropic` is only present with the `extraction` group.
        from anthropic import (  # noqa: PLC0415  # pyright: ignore[reportMissingImports]
            AnthropicBedrock,
        )
    except ImportError as exc:
        msg = (
            "anthropic[bedrock] is required for live Bedrock calls; install "
            "the 'extraction' dependency group: `uv sync --group extraction`"
        )
        raise ImportError(msg) from exc

    aws_region = region or os.environ.get("AWS_REGION")
    raw_client = AnthropicBedrock(aws_region=aws_region, max_retries=_MAX_RETRIES)
    return AnthropicBedrockExtractionClient(raw_client)
