"""Smoke test for the PDF extractor public surface.

Asserts that:

1. The public symbols can be imported from ``where_tickets.extraction``.
2. Calling :func:`extract_pdf` on a rasterized fixture exercises the full
   PATH B (vision) wiring end-to-end with a scripted fake client — proving
   the public surface composes cleanly without touching live Bedrock.

A committed Layer 1 rasterized PDF is copied into ``tmp_path`` so PyMuPDF
returns empty text and the orchestrator routes through PATH B. The
:data:`extract._client_factory` test seam is monkey-patched to a
:class:`FakeBedrockExtractionClient` so neither the Sonnet vision call nor
the Haiku vision-leg call leaves the process.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

# pymupdf ships only with the optional ``extraction`` dep group; without it
# ``pdf.extract_text`` cannot be called. Skip the whole module when absent so
# ``just test`` (no extraction group) collects-but-skips cleanly.
pytest.importorskip("pymupdf")
pytest.importorskip("jsonschema")

from where_tickets.extraction import (  # noqa: E402 — importorskip gate
    ExtractionFailedError,
    extract_pdf,
)
from where_tickets.extraction import extract  # noqa: E402 — importorskip gate
from where_tickets.extraction.bedrock import (  # noqa: E402 — importorskip gate
    ToolUseResult,
    Usage,
)
from where_tickets.extraction.prompts import (  # noqa: E402 — importorskip gate
    TOOL_EMIT_EXTRACTED_FIELDS_NAME,
)

from tests.extraction.fakes import (  # noqa: E402 — importorskip gate
    FakeBedrockExtractionClient,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIOS_DIR = REPO_ROOT / "corpus" / "pdf" / "layer1" / "scenarios"


def _first_rasterized_pdf() -> Path:
    """Pick any committed Layer 1 ``pdf_kind: rasterized`` scenario's PDF."""
    for scenario_dir in sorted(SCENARIOS_DIR.iterdir()):
        if not scenario_dir.is_dir():
            continue
        expected = scenario_dir / "expected-fields.json"
        if not expected.exists():
            continue
        payload = json.loads(expected.read_text())
        if payload.get("pdf_kind") == "rasterized":
            return scenario_dir / "document.pdf"
    msg = "corpus must contain at least one pdf_kind=rasterized scenario"
    raise RuntimeError(msg)


def _valid_payload() -> dict[str, Any]:
    """A minimal hand-built, schema-valid payload for the smoke test."""
    return {
        "document_type": "hotel_booking",
        "cities": ["Paris"],
        "stations": [],
        "accommodations": [
            {
                "city": "Paris",
                "kind": "hotel",
                "identifier": "Hotel Smoke Test",
                "check_in_datetime": "2027-03-11T15:00:00",
                "check_out_datetime": "2027-03-13T11:00:00",
            }
        ],
        "venues": [],
        "travelers": ["Pierre Dubois"],
        "prices": [],
        "qr_codes": [],
        "pdf_kind": "text",
    }


def test_extract_pdf_returns_via_vision_path_on_rasterized_fixture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Rasterized fixture → empty text → PATH B → tagged payload.

    The Slice 5 placeholder raise on the empty-text branch is gone; Slice 6
    wires the real vision path. We use the scripted fake to assert the public
    surface composes (extract_pdf → vision call → haiku call → tagged
    payload) without leaving the process.
    """
    pdf_path = tmp_path / "document.pdf"
    shutil.copy(_first_rasterized_pdf(), pdf_path)

    fake = FakeBedrockExtractionClient(
        vision_responses=["RAW OCR TEXT (smoke fake)"],
        text_responses=[
            ToolUseResult(
                tool_name=TOOL_EMIT_EXTRACTED_FIELDS_NAME,
                tool_input=_valid_payload(),
                usage=Usage(input_tokens=10, output_tokens=5),
                latency_seconds=0.01,
            )
        ],
    )
    monkeypatch.setattr(extract, "_client_factory", lambda: fake)

    result = extract_pdf(pdf_path)
    result_dict: dict[str, Any] = dict(result)
    assert result_dict["extraction_path"] == "vision"
    assert result_dict["pdf_kind"] == "rasterized"


def test_extraction_failed_error_is_a_public_symbol() -> None:
    """:class:`ExtractionFailedError` is importable from the package root."""
    assert issubclass(ExtractionFailedError, Exception)
