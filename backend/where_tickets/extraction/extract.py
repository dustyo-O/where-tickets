"""PDF extractor entry point — Path A live (Slice 5 of spec 006).

This module owns the public surface the corpus runner (and, later, the SQS
pipeline) calls into: :func:`extract_pdf`, :class:`ExtractedFields`, and
:class:`ExtractionFailedError`. The :class:`ExtractedFields` TypedDict mirrors
the runner's ``ExtractedFields`` in ``corpus/pdf/runner.py`` exactly — the
runner is a script, not an importable package, so the contract is duplicated
here rather than imported.

Slice 5 wires PATH A only: text-bearing PDFs go through a single
Haiku-on-text tool-use call. The three placeholder raises below are the
exact lines Slice 6 (vision) and Slice 7 (Sonnet text fallback) will replace
with their respective code paths. Each raise carries a distinct message so
test assertions (and the corpus runner's per-failure diff) can branch on it:

- empty text → ``"empty text; vision path not implemented"``
- sentinel   → ``"sentinel; vision path not implemented"``
- schema-fail / wrong tool → ``"schema fail; sonnet fallback not implemented"``

A module-level ``_client_factory`` provides the test-injection seam: it
defaults to :func:`bedrock.make_client` and tests monkey-patch it to return a
:class:`tests.extraction.fakes.FakeBedrockExtractionClient`.

Every call emits ONE structured log line via the stdlib :mod:`logging`
module (per tech-spec §2.7). The shape is identical across the success path
and the three failure paths so CloudWatch / a log aggregator can rely on a
fixed field set.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict

# `bedrock` is lazy-import-safe (no `anthropic` at module top, no `jsonschema`)
# so we can import it eagerly. `pdf`, `prompts`, and `schema` pull in PyMuPDF
# and `jsonschema` at import time, which only ship with the optional
# `extraction` dep group; import them LAZILY inside :func:`extract_pdf` so the
# Slice 4 bedrock-wrapper tests (which intentionally exercise the lazy-import
# contract without the `extraction` group) can still import this module.
from where_tickets.extraction.bedrock import (
    BedrockExtractionClient,
    Usage,
    make_client,
)

__all__ = [
    "AccommodationEntry",
    "ExtractedFields",
    "ExtractionFailedError",
    "PriceEntry",
    "StationEntry",
    "VenueEntry",
    "extract_pdf",
]


_log = logging.getLogger(__name__)


# Test seam: tests swap this to return a FakeBedrockExtractionClient. Keep the
# underscore prefix — it's private API, but tests reach into it deliberately
# via ``monkeypatch.setattr``. Defaults to the lazy-import-backed factory in
# :mod:`bedrock` so production callers get a real Bedrock-backed client.
_client_factory: Callable[[], BedrockExtractionClient] = make_client


class ExtractionFailedError(Exception):
    """Raised when every extraction path (text, vision, …) has failed.

    Callers should treat this as "the PDF couldn't be read" and surface the
    document as unread to the user, rather than dropping it silently.
    """


class StationEntry(TypedDict):
    city: str
    kind: Literal["airport", "rail_station", "bus_terminal"]
    identifier: str
    departure_datetime: NotRequired[str]
    arrival_datetime: NotRequired[str]


class AccommodationEntry(TypedDict):
    city: str
    kind: Literal["hotel", "airbnb"]
    identifier: str
    check_in_datetime: str
    check_out_datetime: str


class VenueEntry(TypedDict):
    city: str
    kind: Literal["sightseeing", "parking", "other"]
    identifier: str
    valid_from_datetime: NotRequired[str]
    valid_to_datetime: NotRequired[str]


class PriceEntry(TypedDict):
    amount: float
    currency: str


class ExtractedFields(TypedDict):
    document_type: Literal[
        "air_ticket",
        "rail_ticket",
        "bus_ticket",
        "hotel_booking",
        "airbnb_booking",
        "supplementary",
    ]
    cities: list[str]
    stations: list[StationEntry]
    accommodations: list[AccommodationEntry]
    venues: list[VenueEntry]
    travelers: list[str]
    prices: list[PriceEntry]
    qr_codes: list[str]
    pdf_kind: Literal["text", "rasterized"]
    extraction_path: NotRequired[Literal["text", "vision"]]


# `_tag`'s call site only ever uses ``"text"`` in Slice 5; Slice 6's vision
# path will pass ``"vision"``. Kept narrow rather than ``str`` to keep the
# downstream TypedDict's Literal in sync.
_ExtractionPath = Literal["text", "vision"]


def extract_pdf(pdf_path: Path) -> ExtractedFields:
    """Extract structured fields from a single PDF.

    Slice 5 implements PATH A (Haiku on printed text). Empty text, sentinel,
    and schema-mismatch cases raise distinct :class:`ExtractionFailedError`
    messages that Slice 6 (vision) and Slice 7 (Sonnet text fallback) will
    replace with their respective code paths.

    Emits one structured ``"extract_pdf"`` log line per call (success or
    failure), shaped per tech-spec §2.7.
    """
    # Lazy local imports — see module docstring: pdf/prompts/schema pull in
    # PyMuPDF and `jsonschema`, both optional-group-only.
    from where_tickets.extraction import pdf, prompts, schema  # noqa: PLC0415

    started = time.perf_counter()
    text = pdf.extract_text(pdf_path)
    pdf_page_count = pdf.page_count(pdf_path)

    if not text:
        reason = "empty text; vision path not implemented"
        _log_call(
            pdf_path=pdf_path,
            extraction_path=None,
            model_path="failed",
            sentinel_fired=False,
            latency_ms_total=_ms_since(started),
            latency_ms_per_call=[],
            usages=[],
            pdf_page_count=pdf_page_count,
            error_reason=reason,
        )
        raise ExtractionFailedError(reason)

    client = _client_factory()
    call_started = time.perf_counter()
    result = client.complete_text(
        model_alias="haiku",
        system=prompts.SYSTEM_PROMPT_TEXT,
        user_text=text,
        tools=[
            prompts.TOOL_EMIT_EXTRACTED_FIELDS,
            prompts.TOOL_REPORT_NO_USEFUL_INFORMATION,
        ],
        tool_choice={"type": "any"},
    )
    call_latency_ms = _ms_since(call_started)

    if result.tool_name == prompts.TOOL_REPORT_NO_USEFUL_INFORMATION_NAME:
        reason = "sentinel; vision path not implemented"
        _log_call(
            pdf_path=pdf_path,
            extraction_path=None,
            model_path="failed",
            sentinel_fired=True,
            latency_ms_total=_ms_since(started),
            latency_ms_per_call=[call_latency_ms],
            usages=[result.usage],
            pdf_page_count=pdf_page_count,
            error_reason=reason,
        )
        raise ExtractionFailedError(reason)

    if result.tool_name != prompts.TOOL_EMIT_EXTRACTED_FIELDS_NAME:
        reason = "schema fail; sonnet fallback not implemented"
        _log_call(
            pdf_path=pdf_path,
            extraction_path=None,
            model_path="failed",
            sentinel_fired=False,
            latency_ms_total=_ms_since(started),
            latency_ms_per_call=[call_latency_ms],
            usages=[result.usage],
            pdf_page_count=pdf_page_count,
            error_reason=reason,
        )
        raise ExtractionFailedError(reason)

    ok, _errors = schema.validate(result.tool_input)
    if not ok:
        reason = "schema fail; sonnet fallback not implemented"
        _log_call(
            pdf_path=pdf_path,
            extraction_path=None,
            model_path="failed",
            sentinel_fired=False,
            latency_ms_total=_ms_since(started),
            latency_ms_per_call=[call_latency_ms],
            usages=[result.usage],
            pdf_page_count=pdf_page_count,
            error_reason=reason,
        )
        raise ExtractionFailedError(reason)

    payload = _tag(result.tool_input, extraction_path="text")
    _log_call(
        pdf_path=pdf_path,
        extraction_path="text",
        model_path="haiku-text",
        sentinel_fired=False,
        latency_ms_total=_ms_since(started),
        latency_ms_per_call=[call_latency_ms],
        usages=[result.usage],
        pdf_page_count=pdf_page_count,
        error_reason=None,
    )
    return payload


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


def _tag(
    payload: dict[str, Any], *, extraction_path: _ExtractionPath
) -> ExtractedFields:
    """Strip corpus-only metadata and stamp ``extraction_path`` on the payload.

    ``scenario_id`` and ``noise_seed`` are corpus-only metadata fields the
    model occasionally echoes back from example values in the prompt; the
    schema doesn't require them, so we drop them defensively before returning
    the payload upstream.
    """
    cleaned = {k: v for k, v in payload.items() if k not in {"scenario_id", "noise_seed"}}
    cleaned["extraction_path"] = extraction_path
    return cleaned  # type: ignore[return-value]


def _ms_since(t0: float) -> int:
    """Return integer milliseconds elapsed since ``t0`` (``time.perf_counter()``)."""
    return int((time.perf_counter() - t0) * 1000)


_ModelPath = Literal[
    "haiku-text", "sonnet-vision+haiku-text", "sonnet-text", "failed"
]


def _log_call(
    *,
    pdf_path: Path,
    extraction_path: _ExtractionPath | None,
    model_path: _ModelPath,
    sentinel_fired: bool,
    latency_ms_total: int,
    latency_ms_per_call: list[int],
    usages: list[Usage],
    pdf_page_count: int,
    error_reason: str | None,
) -> None:
    """Emit the one ``"extract_pdf"`` structured log line per §2.7.

    The shape is identical across every call site: callers always pass all
    fields, even ``None`` ones, so log consumers can rely on a fixed key set.
    Token counts are summed across the calls in ``usages`` (Slice 5 has at
    most one; Slice 6+ accumulates Sonnet vision + Haiku text).
    """
    _log.info(
        "extract_pdf",
        extra={
            "pdf_path": str(pdf_path),
            "extraction_path": extraction_path,
            "model_path": model_path,
            "sentinel_fired": sentinel_fired,
            "latency_ms_total": latency_ms_total,
            "latency_ms_per_call": latency_ms_per_call,
            "tokens_input": sum(u.input_tokens for u in usages),
            "tokens_output": sum(u.output_tokens for u in usages),
            "tokens_cache_read": sum(u.cache_read_input_tokens for u in usages),
            "tokens_cache_creation": sum(
                u.cache_creation_input_tokens for u in usages
            ),
            "pdf_page_count": pdf_page_count,
            "error_reason": error_reason,
        },
    )
