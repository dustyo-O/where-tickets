"""Slice 7 — PATH C (Sonnet-on-text fallback) control-flow tests.

The two branches exercised here:

1. Haiku-on-text returns an ``emit_extracted_fields`` payload that fails
   schema validation → ``extract_pdf`` re-issues the SAME text to Sonnet
   with only ``emit_extracted_fields`` exposed (no sentinel); Sonnet's
   payload validates → result tagged ``extraction_path="text"`` with
   ``model_path="sonnet-text"``. Asserts the Sonnet call shape (model
   alias, single tool, ``tool_choice="any"``, identical ``user_text``),
   that NO vision call was made, and that the structured log line carries
   the documented 2-element ``latency_ms_per_call`` + summed usage.

2. Both Haiku-on-text AND Sonnet-on-text return invalid payloads →
   :class:`ExtractionFailedError` with the message ``"sonnet text fallback
   produced invalid payload"`` and ``model_path="failed"``.

The total-failure cases (text-path AND vision-path) also live in
``test_extract_failure.py``, where the assertions focus on the failure
contract end-to-end across both paths. The overlap with case (2) here is
deliberate — see ``test_extract_failure.py``'s docstring for the
split-of-responsibility decision.

Gated with ``pytest.importorskip("jsonschema")`` + ``"pymupdf"`` so
``just test`` (no extraction group) collects-but-skips, matching the
pattern used by ``test_extract_text_path.py`` and ``test_extract_vision_path.py``.
"""

from __future__ import annotations

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
    TOOL_EMIT_EXTRACTED_FIELDS_NAME,
)

from tests.extraction.fakes import (  # noqa: E402 — importorskip gate
    FakeBedrockExtractionClient,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIOS_DIR = REPO_ROOT / "corpus" / "pdf" / "layer1" / "scenarios"

# A specific text-bearing fixture — pinned by name so the test stays
# deterministic across regenerations of the corpus. Matches the choice in
# ``test_extract_text_path.py`` and ``test_extract_vision_path.py``.
_TEXT_FIXTURE_DIR = SCENARIOS_DIR / "001-air-1leg-1pax-paris-lisbon"


# Tech-spec §2.7 — the fixed set of keys every ``extract_pdf`` log line emits.
# Kept in sync with the matching constant in the sibling test files.
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


def _valid_air_payload() -> dict[str, Any]:
    """A hand-built, schema-valid air-ticket payload for the Sonnet success leg."""
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
# 1. Haiku invalid → Sonnet valid → returned with extraction_path="text"
# --------------------------------------------------------------------------- #


def test_haiku_invalid_sonnet_valid(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """Haiku returns an invalid payload; Sonnet rescues with a valid one."""
    pdf_path = _copy_fixture(tmp_path, _TEXT_FIXTURE_DIR / "document.pdf")
    # Missing every required field → guaranteed to fail schema.validate.
    invalid_payload: dict[str, Any] = {"document_type": "air_ticket"}
    valid_payload = _valid_air_payload()
    fake = FakeBedrockExtractionClient(
        text_responses=[
            # First text call (Haiku-on-text): schema-fail.
            ToolUseResult(
                tool_name=TOOL_EMIT_EXTRACTED_FIELDS_NAME,
                tool_input=invalid_payload,
                usage=Usage(
                    input_tokens=120,
                    output_tokens=20,
                    cache_read_input_tokens=4,
                    cache_creation_input_tokens=1,
                ),
                latency_seconds=0.3,
            ),
            # Second text call (Sonnet-on-text): valid payload.
            ToolUseResult(
                tool_name=TOOL_EMIT_EXTRACTED_FIELDS_NAME,
                tool_input=valid_payload,
                usage=Usage(
                    input_tokens=250,
                    output_tokens=90,
                    cache_read_input_tokens=6,
                    cache_creation_input_tokens=2,
                ),
                latency_seconds=0.5,
            ),
        ],
    )
    monkeypatch.setattr(extract, "_client_factory", lambda: fake)

    with caplog.at_level(logging.INFO, logger="where_tickets.extraction.extract"):
        result = extract_pdf(pdf_path)

    # --- Returned payload ------------------------------------------------- #
    # Project to a plain dict because ``extraction_path`` is a ``NotRequired``
    # TypedDict key and pyright won't let us subscript it directly.
    result_dict: dict[str, Any] = dict(result)
    assert result_dict["extraction_path"] == "text"
    assert result_dict["document_type"] == valid_payload["document_type"]
    assert result_dict["cities"] == valid_payload["cities"]
    # PATH C trusts the model's `pdf_kind` (unlike PATH B which forces
    # "rasterized") — the input text WAS readable, after all.
    assert result_dict["pdf_kind"] == "text"

    # --- Call sequence ---------------------------------------------------- #
    # Two text calls: Haiku-on-text + Sonnet-on-text. No vision.
    assert len(fake.text_calls) == 2
    assert fake.vision_calls == []

    haiku_call = fake.text_calls[0]
    sonnet_call = fake.text_calls[1]

    # First call: PATH A shape (Haiku with both tools).
    assert haiku_call["model_alias"] == "haiku"

    # Second call: PATH C shape — Sonnet, single tool, identical user_text.
    assert sonnet_call["model_alias"] == "sonnet"
    assert sonnet_call["system"] == SYSTEM_PROMPT_TEXT
    assert sonnet_call["tool_choice"] == {"type": "any"}
    sonnet_tool_names = [tool["name"] for tool in sonnet_call["tools"]]
    assert sonnet_tool_names == [TOOL_EMIT_EXTRACTED_FIELDS_NAME], (
        "PATH C must NOT expose the sentinel tool to Sonnet"
    )
    assert sonnet_call["user_text"] == haiku_call["user_text"], (
        "PATH C must re-issue the SAME text Haiku saw"
    )

    # --- Structured log line --------------------------------------------- #
    extras = _find_log_extras(caplog)
    assert extras["extraction_path"] == "text"
    assert extras["model_path"] == "sonnet-text"
    assert extras["sentinel_fired"] is False
    assert extras["error_reason"] is None
    # PATH C → 2 calls (Haiku-on-text + Sonnet-on-text).
    assert isinstance(extras["latency_ms_per_call"], list)
    assert len(extras["latency_ms_per_call"]) == 2
    # Tokens summed across BOTH text calls.
    assert extras["tokens_input"] == 120 + 250
    assert extras["tokens_output"] == 20 + 90
    assert extras["tokens_cache_read"] == 4 + 6
    assert extras["tokens_cache_creation"] == 1 + 2
    assert extras["pdf_page_count"] >= 1


# --------------------------------------------------------------------------- #
# 2. Haiku invalid → Sonnet ALSO invalid → ExtractionFailedError
# --------------------------------------------------------------------------- #


def test_haiku_invalid_sonnet_also_invalid(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """Both Haiku and Sonnet schema-fail on text → hard failure."""
    pdf_path = _copy_fixture(tmp_path, _TEXT_FIXTURE_DIR / "document.pdf")
    # Missing every required field → guaranteed to fail schema.validate.
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

    # Both text calls were issued before the failure surfaced; no vision.
    assert len(fake.text_calls) == 2
    assert fake.vision_calls == []

    extras = _find_log_extras(caplog)
    assert extras["extraction_path"] is None
    assert extras["model_path"] == "failed"
    assert extras["sentinel_fired"] is False
    assert extras["error_reason"] == "sonnet text fallback produced invalid payload"
    assert len(extras["latency_ms_per_call"]) == 2
    assert extras["tokens_input"] == 120 + 250
    assert extras["tokens_output"] == 20 + 90
