"""Slice 7 — total-failure contract across BOTH paths (text and vision).

After Slice 7 every :class:`ExtractionFailedError` from ``extract_pdf`` is a
genuine total-failure case (the placeholder "not implemented" raises are
gone). This file pins the failure contract down end-to-end so we catch
regressions where one path silently stops emitting the documented log line
or stops carrying the right ``error_reason``.

Two worst-case scenarios, one per path:

1. **Text path total failure** — Haiku-on-text returns an invalid payload,
   PATH C re-issues to Sonnet, Sonnet also returns invalid → raise
   ``ExtractionFailedError("sonnet text fallback produced invalid payload")``
   with ``model_path="failed"`` and 2 latency entries.

2. **Vision path total failure** — rasterized fixture → empty text → PATH B
   vision OCR → vision-leg Haiku returns invalid payload → raise
   ``ExtractionFailedError("vision path produced invalid payload")`` with
   ``model_path="failed"`` and 2 latency entries.

Overlap note: ``test_extract_sonnet_fallback.py`` and
``test_extract_vision_path.py`` each already cover their respective failure
case individually. **The overlap is intentional, with distinct assertion
focus:**

- The path-specific files assert the path's full behavior on failure (call
  shape, tool list, system prompt, that no further fallback is attempted).
- THIS file asserts the cross-path symmetry of the failure contract — that
  both paths produce an ``ExtractionFailedError`` with the documented
  reason string, that both log a ``model_path="failed"`` line with the
  matching ``error_reason``, and that the log line shape is identical
  across the two paths. If a future slice changes the failure contract
  for only one path, this file fails first and points at the divergence.

Gated with ``pytest.importorskip("jsonschema")`` + ``"pymupdf"`` so
``just test`` (no extraction group) collects-but-skips.
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
    TOOL_EMIT_EXTRACTED_FIELDS_NAME,
)

from tests.extraction.fakes import (  # noqa: E402 — importorskip gate
    FakeBedrockExtractionClient,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIOS_DIR = REPO_ROOT / "corpus" / "pdf" / "layer1" / "scenarios"

# Pinned text-bearing fixture; matches the sibling test files.
_TEXT_FIXTURE_DIR = SCENARIOS_DIR / "001-air-1leg-1pax-paris-lisbon"


# Tech-spec §2.7 — the fixed set of keys every ``extract_pdf`` log line emits.
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
    """Return the ``"extract_pdf"`` record's ``extra`` fields as a flat dict."""
    for record in caplog.records:
        if record.message == "extract_pdf":
            return {key: getattr(record, key) for key in _LOG_EXTRA_KEYS}
    msg = (
        "expected an 'extract_pdf' log record from extract_pdf, but only saw: "
        f"{[r.message for r in caplog.records]!r}"
    )
    raise AssertionError(msg)


# --------------------------------------------------------------------------- #
# 1. Text-path total failure: Haiku invalid → Sonnet invalid → raise
# --------------------------------------------------------------------------- #


def test_haiku_invalid_then_sonnet_invalid_raises(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """Text-path worst case: both Haiku and Sonnet fail schema validation."""
    pdf_path = _copy_fixture(tmp_path, _TEXT_FIXTURE_DIR / "document.pdf")
    # Missing every required field → guaranteed schema.validate failure.
    invalid_payload: dict[str, Any] = {"document_type": "air_ticket"}
    fake = FakeBedrockExtractionClient(
        text_responses=[
            ToolUseResult(
                tool_name=TOOL_EMIT_EXTRACTED_FIELDS_NAME,
                tool_input=invalid_payload,
                usage=Usage(input_tokens=120, output_tokens=20),
                latency_seconds=0.3,
            ),
            ToolUseResult(
                tool_name=TOOL_EMIT_EXTRACTED_FIELDS_NAME,
                tool_input=invalid_payload,
                usage=Usage(input_tokens=250, output_tokens=90),
                latency_seconds=0.5,
            ),
        ],
    )
    monkeypatch.setattr(extract, "_client_factory", lambda: fake)

    with (
        caplog.at_level(logging.INFO, logger="where_tickets.extraction.extract"),
        pytest.raises(
            ExtractionFailedError,
            match=r"sonnet text fallback produced invalid payload",
        ),
    ):
        extract_pdf(pdf_path)

    # Cross-path failure-contract assertions: shape of the log line.
    extras = _find_log_extras(caplog)
    assert extras["extraction_path"] is None
    assert extras["model_path"] == "failed"
    assert extras["error_reason"] == "sonnet text fallback produced invalid payload"
    # Sentinel did NOT fire — the trigger was schema-fail, not the sentinel tool.
    assert extras["sentinel_fired"] is False
    # The log line carries every documented key.
    for key in _LOG_EXTRA_KEYS:
        assert key in extras, f"missing log key {key!r} on text-path failure"


# --------------------------------------------------------------------------- #
# 2. Vision-path total failure: empty text → vision Haiku invalid → raise
# --------------------------------------------------------------------------- #


def test_empty_text_then_vision_invalid_raises(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """Vision-path worst case: empty text → vision OCR → invalid Haiku."""
    pdf_path = _copy_fixture(tmp_path, _first_rasterized_pdf())
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
            ExtractionFailedError,
            match=r"vision path produced invalid payload",
        ),
    ):
        extract_pdf(pdf_path)

    # Cross-path failure-contract assertions: shape of the log line mirrors
    # the text-path failure above. Any divergence means the failure contract
    # has drifted between PATH B and PATH C.
    extras = _find_log_extras(caplog)
    assert extras["extraction_path"] is None
    assert extras["model_path"] == "failed"
    assert extras["error_reason"] == "vision path produced invalid payload"
    # Sentinel did NOT fire — the trigger was empty text, not the sentinel tool.
    assert extras["sentinel_fired"] is False
    for key in _LOG_EXTRA_KEYS:
        assert key in extras, f"missing log key {key!r} on vision-path failure"
