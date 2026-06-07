"""Slice 6 — PATH B (Sonnet vision + Haiku-on-OCR-text) control-flow tests.

The three branches exercised here:

1. Empty text (rasterized fixture) → PATH B triggered by empty PyMuPDF text.
   Vision call sent to Sonnet, OCR'd text fed into a Haiku tool-use call,
   valid payload returned with ``extraction_path="vision"``. Asserts vision-
   call shape (JPEG bytes, model alias, system + user prompt), Haiku-leg
   call shape (single-tool list, ``tool_choice="any"``, raw OCR text as the
   user message), the structured log line (model_path, sentinel_fired=False,
   2 latency entries, summed usage), and that ``pdf_kind="rasterized"`` is
   forced onto the payload.

2. Sentinel-triggered PATH B → a text-bearing fixture's first Haiku-on-text
   call returns ``report_no_useful_information``; the orchestrator falls
   through to vision; the vision-leg Haiku call returns a valid payload;
   final result is ``extraction_path="vision"``. Asserts the log line has
   ``sentinel_fired=True``, 3 latency entries (haiku-text + sonnet-vision +
   haiku-text-via-vision), and usage summed across the haiku-text calls.

3. Vision-leg invalid payload → :class:`ExtractionFailedError` with the
   message ``"vision path produced invalid payload"``. No Sonnet-text
   fallback from the vision path (per tech-spec §2.2).

Gated with ``pytest.importorskip("jsonschema")`` + ``"pymupdf"`` so
``just test`` (no extraction group) collects-but-skips, matching the
pattern used by ``test_extract_text_path.py`` and ``test_pdf_helpers.py``.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

import pytest

# `jsonschema` + `pymupdf` ship only with the optional `extraction` dep group.
pytest.importorskip("jsonschema")
pytest.importorskip("pymupdf")

from where_tickets.extraction import extract  # noqa: E402 — importorskip gate
from where_tickets.extraction.bedrock import (  # noqa: E402 — importorskip gate
    ToolUseResult,
    Usage,
)
from where_tickets.extraction.extract import (  # noqa: E402 — importorskip gate
    ExtractionFailedError,
    extract_pdf,
)
from where_tickets.extraction.prompts import (  # noqa: E402 — importorskip gate
    SYSTEM_PROMPT_TEXT,
    SYSTEM_PROMPT_VISION,
    TOOL_EMIT_EXTRACTED_FIELDS_NAME,
    TOOL_REPORT_NO_USEFUL_INFORMATION_NAME,
    VISION_USER_PROMPT,
)

from tests.extraction.fakes import (  # noqa: E402 — importorskip gate
    FakeBedrockExtractionClient,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIOS_DIR = REPO_ROOT / "corpus" / "pdf" / "layer1" / "scenarios"

# A specific text-bearing fixture — pinned by name so the sentinel-trigger
# test stays deterministic across corpus regenerations. Mirrors the choice in
# ``test_extract_text_path.py``.
_TEXT_FIXTURE_DIR = SCENARIOS_DIR / "001-air-1leg-1pax-paris-lisbon"

# JPEG SOI marker — every well-formed JPEG starts with these three bytes.
# Used to confirm the vision call received real rendered image bytes rather
# than something accidentally re-encoded by the test scaffolding.
_JPEG_SOI = b"\xff\xd8\xff"

# Tech-spec §2.7 — the fixed set of keys every ``extract_pdf`` log line emits.
# Kept in sync with the matching constant in ``test_extract_text_path.py``.
_LOG_EXTRA_KEYS: tuple[str, ...] = (
    "pdf_path",
    "extraction_path",
    "model_path",
    "sentinel_fired",
    "latency_ms_total",
    "latency_ms_per_call",
    "tokens_input",
    "tokens_output",
    "tokens_cache_read",
    "tokens_cache_creation",
    "pdf_page_count",
    "error_reason",
)


def _first_rasterized_pdf() -> Path:
    """Return any committed Layer 1 ``pdf_kind: rasterized`` fixture's PDF."""
    for scenario_dir in sorted(SCENARIOS_DIR.iterdir()):
        if not scenario_dir.is_dir():
            continue
        expected = scenario_dir / "expected-fields.json"
        if not expected.exists():
            continue
        payload = json.loads(expected.read_text())
        if payload.get("pdf_kind") == "rasterized":
            return scenario_dir / "document.pdf"
    pytest.skip("corpus has no rasterized scenarios")


def _copy_fixture(tmp_path: Path, src: Path) -> Path:
    """Copy ``src`` into ``tmp_path`` so the extractor reads a fresh file."""
    dst = tmp_path / "document.pdf"
    shutil.copy(src, dst)
    return dst


def _find_log_extras(caplog: pytest.LogCaptureFixture) -> dict[str, Any]:
    """Return the ``"extract_pdf"`` record's ``extra`` fields as a flat dict.

    pyright doesn't know about the dynamic attributes stdlib ``logging``
    grafts onto :class:`logging.LogRecord` from a call's ``extra=`` kwarg, so
    we project them off the record into a plain ``dict[str, Any]`` and have
    tests assert against the dict.
    """
    for record in caplog.records:
        if record.message == "extract_pdf":
            return {key: getattr(record, key) for key in _LOG_EXTRA_KEYS}
    msg = (
        "expected an 'extract_pdf' log record from extract_pdf, but only saw: "
        f"{[r.message for r in caplog.records]!r}"
    )
    raise AssertionError(msg)


def _valid_hotel_payload() -> dict[str, Any]:
    """A hand-built, schema-valid hotel-booking payload for vision tests.

    Hotels are a natural match for ``pdf_kind="rasterized"`` rasterized
    fixtures in the committed corpus; the value here isn't tied to any
    specific fixture's expected output — it just needs to validate.
    """
    return {
        "document_type": "hotel_booking",
        "cities": ["Paris"],
        "stations": [],
        "accommodations": [
            {
                "city": "Paris",
                "kind": "hotel",
                "identifier": "Hotel de Test",
                "check_in_datetime": "2027-03-11T15:00:00",
                "check_out_datetime": "2027-03-13T11:00:00",
            }
        ],
        "venues": [],
        "travelers": ["Pierre Dubois"],
        "prices": [],
        "qr_codes": [],
        # Intentionally set to "text" so we can prove `_run_vision_path`
        # overrides it to "rasterized" on the way out.
        "pdf_kind": "text",
    }


def _valid_air_payload() -> dict[str, Any]:
    """A hand-built, schema-valid air-ticket payload for the sentinel test."""
    return {
        "document_type": "air_ticket",
        "cities": ["Paris", "Lisbon"],
        "stations": [
            {
                "city": "Paris",
                "kind": "airport",
                "identifier": "CDG",
                "departure_datetime": "2027-03-11T08:30:00",
            },
            {
                "city": "Lisbon",
                "kind": "airport",
                "identifier": "LIS",
                "arrival_datetime": "2027-03-11T10:45:00",
            },
        ],
        "accommodations": [],
        "venues": [],
        "travelers": ["Pierre Dubois"],
        "prices": [],
        "qr_codes": [],
        "pdf_kind": "text",
    }


# --------------------------------------------------------------------------- #
# 1. Empty text → PATH B (vision)
# --------------------------------------------------------------------------- #


def test_empty_text_routes_to_vision(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """Rasterized fixture → empty text → vision OCR → valid payload."""
    pdf_path = _copy_fixture(tmp_path, _first_rasterized_pdf())
    raw_ocr = "RAW OCR TEXT FROM SONNET VISION (mocked)"
    payload = _valid_hotel_payload()
    fake = FakeBedrockExtractionClient(
        vision_responses=[raw_ocr],
        text_responses=[
            ToolUseResult(
                tool_name=TOOL_EMIT_EXTRACTED_FIELDS_NAME,
                tool_input=payload,
                usage=Usage(
                    input_tokens=200,
                    output_tokens=80,
                    cache_read_input_tokens=5,
                    cache_creation_input_tokens=2,
                ),
                latency_seconds=0.3,
            )
        ],
    )
    monkeypatch.setattr(extract, "_client_factory", lambda: fake)

    with caplog.at_level(logging.INFO, logger="where_tickets.extraction.extract"):
        result = extract_pdf(pdf_path)

    # --- Returned payload ------------------------------------------------- #
    # Project to a plain dict because ``extraction_path`` is a ``NotRequired``
    # TypedDict key and pyright won't let us subscript it directly.
    result_dict: dict[str, Any] = dict(result)
    assert result_dict["extraction_path"] == "vision"
    # `_run_vision_path` forces `pdf_kind="rasterized"` regardless of what
    # the model emitted — see the comment in `extract._run_vision_path`.
    assert result_dict["pdf_kind"] == "rasterized"
    assert result_dict["document_type"] == payload["document_type"]
    assert result_dict["cities"] == payload["cities"]

    # --- Vision call shape ------------------------------------------------ #
    assert len(fake.vision_calls) == 1
    v_call = fake.vision_calls[0]
    assert v_call["model_alias"] == "sonnet"
    assert v_call["system"] == SYSTEM_PROMPT_VISION
    assert v_call["prompt"] == VISION_USER_PROMPT
    images: list[bytes] = v_call["images"]
    assert len(images) >= 1, "expected at least one rendered page JPEG"
    assert all(isinstance(img, bytes) for img in images)
    assert all(img.startswith(_JPEG_SOI) for img in images), (
        "rendered images should be real JPEG bytes, not placeholders"
    )

    # --- Vision-leg Haiku call shape ------------------------------------- #
    assert len(fake.text_calls) == 1
    t_call = fake.text_calls[0]
    assert t_call["model_alias"] == "haiku"
    assert t_call["system"] == SYSTEM_PROMPT_TEXT
    assert t_call["user_text"] == raw_ocr
    assert t_call["tool_choice"] == {"type": "any"}
    # Critically: NO sentinel tool on the vision-leg Haiku call (per §2.2).
    tool_names = [tool["name"] for tool in t_call["tools"]]
    assert tool_names == [TOOL_EMIT_EXTRACTED_FIELDS_NAME]

    # --- Structured log line --------------------------------------------- #
    extras = _find_log_extras(caplog)
    assert extras["extraction_path"] == "vision"
    assert extras["model_path"] == "sonnet-vision+haiku-text"
    assert extras["sentinel_fired"] is False
    assert extras["error_reason"] is None
    # Empty-text trigger → 2 calls (sonnet-vision + haiku-text-via-vision).
    assert isinstance(extras["latency_ms_per_call"], list)
    assert len(extras["latency_ms_per_call"]) == 2
    # Usage is currently summed across the text calls only — `complete_vision`
    # returns a `str` and doesn't surface its Usage today (Slice 4 contract).
    # Document that here so a future widen of the vision contract updates
    # this assertion intentionally.
    assert extras["tokens_input"] == 200
    assert extras["tokens_output"] == 80
    assert extras["tokens_cache_read"] == 5
    assert extras["tokens_cache_creation"] == 2
    assert extras["pdf_page_count"] >= 1


# --------------------------------------------------------------------------- #
# 2. Sentinel from Haiku-on-text → PATH B (vision) with carry-forward usage
# --------------------------------------------------------------------------- #


def test_sentinel_routes_to_vision(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """Text fixture + sentinel from first Haiku call → vision fallback."""
    pdf_path = _copy_fixture(tmp_path, _TEXT_FIXTURE_DIR / "document.pdf")
    fake = FakeBedrockExtractionClient(
        text_responses=[
            # First call: Haiku-on-text returns the sentinel.
            ToolUseResult(
                tool_name=TOOL_REPORT_NO_USEFUL_INFORMATION_NAME,
                tool_input={"reason": "no travel content visible"},
                usage=Usage(input_tokens=300, output_tokens=20),
                latency_seconds=0.2,
            ),
            # Second call: vision-leg Haiku returns a valid payload.
            ToolUseResult(
                tool_name=TOOL_EMIT_EXTRACTED_FIELDS_NAME,
                tool_input=_valid_air_payload(),
                usage=Usage(input_tokens=400, output_tokens=120),
                latency_seconds=0.4,
            ),
        ],
        vision_responses=["RAW OCR FALLBACK TEXT"],
    )
    monkeypatch.setattr(extract, "_client_factory", lambda: fake)

    with caplog.at_level(logging.INFO, logger="where_tickets.extraction.extract"):
        result = extract_pdf(pdf_path)

    result_dict: dict[str, Any] = dict(result)
    assert result_dict["extraction_path"] == "vision"
    # Forced regardless of what the model emitted on the vision-leg payload.
    assert result_dict["pdf_kind"] == "rasterized"

    # Two text calls: Haiku-on-text (sentinel) + vision-leg Haiku (valid).
    assert len(fake.text_calls) == 2
    assert len(fake.vision_calls) == 1

    # First text call: PATH A shape (both tools, ``tool_choice="any"``).
    first_text = fake.text_calls[0]
    first_tool_names = {tool["name"] for tool in first_text["tools"]}
    assert first_tool_names == {
        TOOL_EMIT_EXTRACTED_FIELDS_NAME,
        TOOL_REPORT_NO_USEFUL_INFORMATION_NAME,
    }
    # Second text call: PATH B Haiku-leg shape (only emit; no sentinel).
    second_text = fake.text_calls[1]
    second_tool_names = [tool["name"] for tool in second_text["tools"]]
    assert second_tool_names == [TOOL_EMIT_EXTRACTED_FIELDS_NAME]

    # --- Structured log line --------------------------------------------- #
    extras = _find_log_extras(caplog)
    assert extras["extraction_path"] == "vision"
    assert extras["model_path"] == "sonnet-vision+haiku-text"
    # Sentinel trigger (NOT empty text) → sentinel_fired must be True.
    assert extras["sentinel_fired"] is True
    assert extras["error_reason"] is None
    # Three calls on this path: haiku-text (sentinel) + sonnet-vision +
    # haiku-text-via-vision.
    assert isinstance(extras["latency_ms_per_call"], list)
    assert len(extras["latency_ms_per_call"]) == 3
    # Usage is summed across the two haiku-text calls (sonnet-vision doesn't
    # surface Usage in the current `complete_vision` contract).
    assert extras["tokens_input"] == 300 + 400
    assert extras["tokens_output"] == 20 + 120


# --------------------------------------------------------------------------- #
# 3. Vision-leg invalid payload → ExtractionFailedError, no Sonnet fallback
# --------------------------------------------------------------------------- #


def test_vision_haiku_invalid_payload_raises(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """Vision-leg Haiku returns an invalid payload → hard failure."""
    pdf_path = _copy_fixture(tmp_path, _first_rasterized_pdf())
    # Missing every required field → guaranteed to fail schema.validate.
    invalid_payload: dict[str, Any] = {"document_type": "air_ticket"}
    fake = FakeBedrockExtractionClient(
        vision_responses=["GARBAGE OCR"],
        text_responses=[
            ToolUseResult(
                tool_name=TOOL_EMIT_EXTRACTED_FIELDS_NAME,
                tool_input=invalid_payload,
                usage=Usage(input_tokens=100, output_tokens=30),
                latency_seconds=0.2,
            )
        ],
    )
    monkeypatch.setattr(extract, "_client_factory", lambda: fake)

    with (
        caplog.at_level(logging.INFO, logger="where_tickets.extraction.extract"),
        pytest.raises(
            ExtractionFailedError, match=r"vision path produced invalid payload"
        ),
    ):
        extract_pdf(pdf_path)

    # Both bedrock calls were issued before the failure surfaced.
    assert len(fake.vision_calls) == 1
    assert len(fake.text_calls) == 1

    extras = _find_log_extras(caplog)
    assert extras["extraction_path"] is None
    assert extras["model_path"] == "failed"
    assert extras["sentinel_fired"] is False
    assert extras["error_reason"] == "vision path produced invalid payload"
    # Empty-text trigger → 2 calls in the latency list.
    assert len(extras["latency_ms_per_call"]) == 2
    assert extras["tokens_input"] == 100
    assert extras["tokens_output"] == 30
