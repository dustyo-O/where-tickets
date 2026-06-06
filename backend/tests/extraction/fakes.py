"""Scripted fakes for the :mod:`where_tickets.extraction.bedrock` seam.

The extractor orchestrator (Slice 5+) depends on the
:class:`BedrockExtractionClient` protocol; tests inject this fake to drive the
control flow without an ``anthropic`` import. Each test queues a script of
canned responses up front and asserts on the recorded calls afterwards.

Exhausting either script raises a clear :class:`RuntimeError` rather than
returning a default — silent defaults hide test setup mistakes (a typical
"why is my test passing for the wrong reason?" footgun).
"""

from __future__ import annotations

from typing import Any

from where_tickets.extraction.bedrock import ToolUseResult, Usage

__all__ = ["FakeBedrockExtractionClient", "ok_text_result"]


def ok_text_result(
    *,
    tool_name: str,
    tool_input: dict[str, Any],
    input_tokens: int = 1000,
    output_tokens: int = 100,
    cache_read_input_tokens: int = 0,
    latency_seconds: float = 0.1,
) -> ToolUseResult:
    """Convenience constructor for a :class:`ToolUseResult` in tests."""
    return ToolUseResult(
        tool_name=tool_name,
        tool_input=tool_input,
        usage=Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
        ),
        latency_seconds=latency_seconds,
    )


class FakeBedrockExtractionClient:
    """Scripted fake satisfying the :class:`BedrockExtractionClient` protocol.

    Build it with a list of canned responses per method; ``complete_text``
    pops from ``text_responses`` in order, ``complete_vision`` pops from
    ``vision_responses``. Every call is recorded in ``text_calls`` /
    ``vision_calls`` so tests can assert what the orchestrator sent.

    Exhausting either script raises a :class:`RuntimeError` with a precise
    message ("called N+1 times but only N responses were queued") so the
    test author gets pointed straight at the missing fixture.
    """

    def __init__(
        self,
        *,
        text_responses: list[ToolUseResult] | None = None,
        vision_responses: list[str] | None = None,
    ) -> None:
        self._text_queue: list[ToolUseResult] = list(text_responses or [])
        self._vision_queue: list[str] = list(vision_responses or [])
        self._text_calls: list[dict[str, Any]] = []
        self._vision_calls: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ #
    # Recorded calls (read-only views for tests to assert on)
    # ------------------------------------------------------------------ #

    @property
    def text_calls(self) -> list[dict[str, Any]]:
        """The kwargs of every ``complete_text`` call, in call order."""
        return self._text_calls

    @property
    def vision_calls(self) -> list[dict[str, Any]]:
        """The kwargs of every ``complete_vision`` call, in call order."""
        return self._vision_calls

    # ------------------------------------------------------------------ #
    # Protocol methods
    # ------------------------------------------------------------------ #

    def complete_text(
        self,
        *,
        model_alias: str,
        system: str,
        user_text: str,
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any],
    ) -> ToolUseResult:
        """Pop the next queued :class:`ToolUseResult`; record the call."""
        self._text_calls.append(
            {
                "model_alias": model_alias,
                "system": system,
                "user_text": user_text,
                "tools": tools,
                "tool_choice": tool_choice,
            }
        )
        if not self._text_queue:
            queued = len(self._text_calls) - 1
            msg = (
                f"FakeBedrockExtractionClient: complete_text called "
                f"{len(self._text_calls)} time(s) but only {queued} "
                f"text_responses were queued"
            )
            raise RuntimeError(msg)
        return self._text_queue.pop(0)

    def complete_vision(
        self,
        *,
        model_alias: str,
        system: str,
        images: list[bytes],
        prompt: str,
    ) -> str:
        """Pop the next queued vision string; record the call."""
        self._vision_calls.append(
            {
                "model_alias": model_alias,
                "system": system,
                "images": images,
                "prompt": prompt,
            }
        )
        if not self._vision_queue:
            queued = len(self._vision_calls) - 1
            msg = (
                f"FakeBedrockExtractionClient: complete_vision called "
                f"{len(self._vision_calls)} time(s) but only {queued} "
                f"vision_responses were queued"
            )
            raise RuntimeError(msg)
        return self._vision_queue.pop(0)
