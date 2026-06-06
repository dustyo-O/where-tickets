"""PDF extractor entry point — PATH A + PATH B live (Slice 6 of spec 006).

This module owns the public surface the corpus runner (and, later, the SQS
pipeline) calls into: :func:`extract_pdf`, :class:`ExtractedFields`, and
:class:`ExtractionFailedError`. The :class:`ExtractedFields` TypedDict mirrors
the runner's ``ExtractedFields`` in ``corpus/pdf/runner.py`` exactly — the
runner is a script, not an importable package, so the contract is duplicated
here rather than imported.

Two of the three placeholder raises from Slice 5 are now real code paths:

- empty text → PATH B (vision): render the PDF pages to JPEGs, OCR via
  Sonnet vision, feed the raw text into a Haiku ``emit_extracted_fields``
  call, validate, tag ``extraction_path="vision"``.
- sentinel → PATH B (vision): the first Haiku-on-text call returned
  ``report_no_useful_information``; we fall through into the same vision
  path. The structured log line records ``sentinel_fired=True`` to
  distinguish this trigger from the empty-text trigger.

The third placeholder raise remains:

- schema-fail / wrong tool → ``"schema fail; sonnet fallback not
  implemented"`` (Slice 7's job).

A module-level ``_client_factory`` provides the test-injection seam: it
defaults to :func:`bedrock.make_client` and tests monkey-patch it to return a
:class:`tests.extraction.fakes.FakeBedrockExtractionClient`. The factory is
called ONCE per :func:`extract_pdf` invocation and the resulting client is
reused for every Bedrock call in that run.

Every call emits ONE structured log line via the stdlib :mod:`logging`
module (per tech-spec §2.7). The shape is identical across the success path
and the failure paths so CloudWatch / a log aggregator can rely on a fixed
field set.
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

    Routes:

    - PATH A — text layer present → Haiku-on-text tool-use call. Valid
      payload → tagged ``extraction_path="text"``; sentinel → fall through
      to PATH B; wrong tool / schema fail → Slice 7's placeholder raise.
    - PATH B — empty text OR sentinel → Sonnet vision OCRs the rendered
      pages, then a Haiku tool-use call extracts fields from the OCR text.
      Schema fail on the vision leg surfaces as
      :class:`ExtractionFailedError` ("vision path produced invalid
      payload") — there is no Sonnet-text fallback from the vision path.

    Emits one structured ``"extract_pdf"`` log line per call (success or
    failure), shaped per tech-spec §2.7. The :data:`_client_factory` is
    invoked ONCE and the resulting client is reused across every Bedrock
    call in this run.
    """
    # Lazy local imports — see module docstring: pdf/prompts/schema pull in
    # PyMuPDF and `jsonschema`, both optional-group-only.
    from where_tickets.extraction import pdf, prompts, schema  # noqa: PLC0415

    started = time.perf_counter()
    text = pdf.extract_text(pdf_path)
    pdf_page_count = pdf.page_count(pdf_path)
    client = _client_factory()

    if not text:
        # PATH B triggered by empty text from PyMuPDF. No prior Bedrock
        # calls have happened yet, so the carry-forward lists are empty.
        return _run_vision_path(
            pdf_path,
            client,
            sentinel_fired=False,
            started=started,
            prior_latency_ms_per_call=[],
            prior_usages=[],
            pdf_page_count=pdf_page_count,
        )

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
        # PATH B triggered by the sentinel. Carry the Haiku-on-text call's
        # latency + usage forward so the final log line sums all three
        # calls (haiku-text + sonnet-vision + haiku-text-via-vision).
        return _run_vision_path(
            pdf_path,
            client,
            sentinel_fired=True,
            started=started,
            prior_latency_ms_per_call=[call_latency_ms],
            prior_usages=[result.usage],
            pdf_page_count=pdf_page_count,
        )

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


def _run_vision_path(
    pdf_path: Path,
    client: BedrockExtractionClient,
    *,
    sentinel_fired: bool,
    started: float,
    prior_latency_ms_per_call: list[int],
    prior_usages: list[Usage],
    pdf_page_count: int,
) -> ExtractedFields:
    """Run PATH B (vision): Sonnet OCR → Haiku tool-use → validate → tag.

    Carry-forwards: when PATH B is triggered by the sentinel (rather than by
    empty text), the caller has already burned one Haiku-on-text call; its
    latency and usage are passed in via ``prior_*`` so the structured log
    line on success can sum everything into one accurate total.

    On schema fail, raises :class:`ExtractionFailedError` with the message
    ``"vision path produced invalid payload"`` — there is no Sonnet-text
    fallback from the vision path (per tech-spec §2.2).
    """
    from where_tickets.extraction import pdf as pdf_mod  # noqa: PLC0415
    from where_tickets.extraction import prompts, schema  # noqa: PLC0415

    # --- Sonnet vision leg -------------------------------------------------
    images = pdf_mod.render_pages_to_jpeg(pdf_path)
    vision_started = time.perf_counter()
    raw_text = client.complete_vision(
        model_alias="sonnet",
        system=prompts.SYSTEM_PROMPT_VISION,
        images=images,
        prompt=prompts.VISION_USER_PROMPT,
    )
    vision_latency_ms = _ms_since(vision_started)

    # --- Haiku vision-leg extraction call ---------------------------------
    # Only ONE tool is exposed (no sentinel here): if Sonnet read pixels and
    # Haiku still can't make sense of them, we surface the failure rather
    # than looping forever. `tool_choice="any"` forces the single tool.
    call_started = time.perf_counter()
    result = client.complete_text(
        model_alias="haiku",
        system=prompts.SYSTEM_PROMPT_TEXT,
        user_text=raw_text,
        tools=[prompts.TOOL_EMIT_EXTRACTED_FIELDS],
        tool_choice={"type": "any"},
    )
    haiku_latency_ms = _ms_since(call_started)

    latency_ms_per_call = [
        *prior_latency_ms_per_call,
        vision_latency_ms,
        haiku_latency_ms,
    ]
    usages = [*prior_usages, result.usage]

    ok = (
        result.tool_name == prompts.TOOL_EMIT_EXTRACTED_FIELDS_NAME
        and schema.validate(result.tool_input)[0]
    )
    if not ok:
        reason = "vision path produced invalid payload"
        _log_call(
            pdf_path=pdf_path,
            extraction_path=None,
            model_path="failed",
            sentinel_fired=sentinel_fired,
            latency_ms_total=_ms_since(started),
            latency_ms_per_call=latency_ms_per_call,
            usages=usages,
            pdf_page_count=pdf_page_count,
            error_reason=reason,
        )
        raise ExtractionFailedError(reason)

    # We KNOW we ran vision, so `pdf_kind` is mechanically `"rasterized"`.
    # Force it rather than trusting the model to pick the right value — the
    # corpus's expected ``pdf_kind`` for these scenarios is always
    # ``"rasterized"``, so any model-guessed ``"text"`` here would be a
    # false negative against the corpus.
    payload = _tag(result.tool_input, extraction_path="vision")
    payload["pdf_kind"] = "rasterized"
    _log_call(
        pdf_path=pdf_path,
        extraction_path="vision",
        model_path="sonnet-vision+haiku-text",
        sentinel_fired=sentinel_fired,
        latency_ms_total=_ms_since(started),
        latency_ms_per_call=latency_ms_per_call,
        usages=usages,
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
