"""Slice 8 — debug CLI (``python -m where_tickets.extraction <pdf-path>``) tests.

Exercises the three exit-code paths of :func:`where_tickets.extraction.cli.main`:

1. Success — Haiku-on-text returns a valid payload. Exit 0; stdout is the
   pretty-JSON :class:`ExtractedFields` (with ``extraction_path="text"``);
   stderr carries the ``extraction_path=text  model_path=haiku-text``
   diagnostic line.
2. Hard failure — Haiku-on-text AND Sonnet-on-text both schema-fail.
   :func:`extract_pdf` raises :class:`ExtractionFailedError`. Exit 1; stdout
   is empty; stderr carries both the ``ExtractionFailedError: ...`` reason
   and a ``model_path=failed`` diagnostic line.
3. PDF not found — exit 2 with a ``PDF not found`` stderr message and NO
   Bedrock calls.

The test seam is the same one Slices 5–7 use: monkeypatch
:data:`where_tickets.extraction.extract._client_factory` to return a
:class:`tests.extraction.fakes.FakeBedrockExtractionClient`. Reusing the
exact seam guarantees the CLI exercises the production code path end-to-end.

Gated with ``pytest.importorskip("jsonschema")`` + ``"pymupdf"`` so
``just test`` (which doesn't install the optional ``extraction`` group)
collects-but-skips, mirroring every other test in this directory.
"""

from __future__ import annotations

import json
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
from where_tickets.extraction.cli import main  # noqa: E402 — importorskip gate
from where_tickets.extraction.prompts import (  # noqa: E402 — importorskip gate
    TOOL_EMIT_EXTRACTED_FIELDS_NAME,
)

from tests.extraction.fakes import (  # noqa: E402 — importorskip gate
    FakeBedrockExtractionClient,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIOS_DIR = REPO_ROOT / "corpus" / "pdf" / "layer1" / "scenarios"

# Pinned text-bearing fixture — same scenario the Slice 5/6/7 tests use, so
# the CLI test stays deterministic across corpus regenerations.
_TEXT_FIXTURE = (
    SCENARIOS_DIR / "001-air-1leg-1pax-paris-lisbon" / "document.pdf"
)


def _valid_payload() -> dict[str, Any]:
    """A hand-built, schema-valid air-ticket payload.

    Mirrors the payload used in the other extract tests so the CLI test
    stays decoupled from any future schema tightening.
    """
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
# 1. Success — exit 0, JSON on stdout, diagnostics on stderr
# --------------------------------------------------------------------------- #


def test_cli_success_prints_json_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Valid Haiku payload → exit 0; pretty JSON on stdout; diagnostics on stderr."""
    fake = FakeBedrockExtractionClient(
        text_responses=[
            ToolUseResult(
                tool_name=TOOL_EMIT_EXTRACTED_FIELDS_NAME,
                tool_input=_valid_payload(),
                usage=Usage(input_tokens=300, output_tokens=120),
                latency_seconds=0.4,
            )
        ],
    )
    monkeypatch.setattr(extract, "_client_factory", lambda: fake)

    exit_code = main([str(_TEXT_FIXTURE)])
    captured = capsys.readouterr()

    assert exit_code == 0

    # --- stdout is pretty JSON of the ExtractedFields -------------------- #
    payload = json.loads(captured.out)
    assert payload["extraction_path"] == "text"
    assert payload["document_type"] == "air_ticket"
    assert payload["cities"] == ["Paris", "Lisbon"]
    assert payload["pdf_kind"] == "text"

    # --- stderr carries the diagnostic line ------------------------------ #
    assert "extraction_path=text" in captured.err
    assert "model_path=haiku-text" in captured.err

    # --- One Haiku call, no vision --------------------------------------- #
    assert len(fake.text_calls) == 1
    assert fake.vision_calls == []


# --------------------------------------------------------------------------- #
# 2. ExtractionFailedError — exit 1, empty stdout, reason on stderr
# --------------------------------------------------------------------------- #


def test_cli_extraction_failure_exits_one(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Both Haiku and Sonnet schema-fail → exit 1; reason + failed model_path on stderr."""
    # Missing every required field → guaranteed to fail schema.validate.
    invalid: dict[str, Any] = {"document_type": "air_ticket", "pdf_kind": "text"}
    fake = FakeBedrockExtractionClient(
        text_responses=[
            ToolUseResult(
                tool_name=TOOL_EMIT_EXTRACTED_FIELDS_NAME,
                tool_input=invalid,
                usage=Usage(input_tokens=300, output_tokens=20),
                latency_seconds=0.2,
            ),
            ToolUseResult(
                tool_name=TOOL_EMIT_EXTRACTED_FIELDS_NAME,
                tool_input=invalid,
                usage=Usage(input_tokens=400, output_tokens=30),
                latency_seconds=0.3,
            ),
        ],
    )
    monkeypatch.setattr(extract, "_client_factory", lambda: fake)

    exit_code = main([str(_TEXT_FIXTURE)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.out == ""
    assert (
        "ExtractionFailedError: sonnet text fallback produced invalid payload"
        in captured.err
    )
    assert "model_path=failed" in captured.err
    # Both text calls fired (Haiku + Sonnet); no vision call.
    assert len(fake.text_calls) == 2
    assert fake.vision_calls == []


# --------------------------------------------------------------------------- #
# 3. PDF not found — exit 2, no Bedrock calls
# --------------------------------------------------------------------------- #


def test_cli_file_not_found_exits_two(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Missing PDF → exit 2 with PDF-not-found stderr; extractor never called."""
    fake = FakeBedrockExtractionClient()  # No scripted responses — never called.
    monkeypatch.setattr(extract, "_client_factory", lambda: fake)

    missing = tmp_path / "does-not-exist.pdf"
    exit_code = main([str(missing)])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "PDF not found" in captured.err
    assert str(missing) in captured.err
    # The fake was never called — assert via the recorded-calls views, NOT by
    # checking the (private) queues. A wrong CLI implementation that briefly
    # touched the client before the existence check would pop a response and
    # leave a recorded call here.
    assert fake.text_calls == []
    assert fake.vision_calls == []
