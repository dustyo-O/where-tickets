"""PATH A (Haiku-on-text) control-flow tests.

The PATH-A happy-path branch exercised here:

1. Valid Haiku tool-use payload → ``extract_pdf`` returns it with
   ``extraction_path="text"``. Asserts the call shape (model alias, tool list,
   tool_choice) and the structured ``"extract_pdf"`` log line.

The sentinel and empty-text branches both route through PATH B (Slice 6) —
see ``test_extract_vision_path.py`` for that coverage. The schema-fail /
wrong-tool branch routes through PATH C (Slice 7) — see
``test_extract_sonnet_fallback.py`` for that coverage; the worst case
(Haiku invalid → Sonnet invalid) lives in ``test_extract_failure.py``.

The test seam: tests swap :data:`where_tickets.extraction.extract._client_factory`
to return a :class:`tests.extraction.fakes.FakeBedrockExtractionClient`.

Gated with ``pytest.importorskip("jsonschema")`` so ``just test`` (no
extraction group) collects-but-skips, matching the pattern used by
``test_schema_contract.py`` and ``test_pdf_helpers.py``.
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
    extract_pdf,
)
from where_tickets.extraction.prompts import (  # noqa: E402 — importorskip gate
    TOOL_EMIT_EXTRACTED_FIELDS_NAME,
    TOOL_REPORT_NO_USEFUL_INFORMATION_NAME,
)

from tests.extraction.fakes import (  # noqa: E402 — importorskip gate
    FakeBedrockExtractionClient,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIOS_DIR = REPO_ROOT / "corpus" / "pdf" / "layer1" / "scenarios"

# A specific text-bearing fixture — pinned by name so the test stays
# deterministic across regenerations of the corpus. Picked from the committed
# Layer 1 scenarios; any ``pdf_kind: "text"`` scenario would work.
_TEXT_FIXTURE_DIR = SCENARIOS_DIR / "001-air-1leg-1pax-paris-lisbon"


def _valid_payload() -> dict[str, Any]:
    """A hand-built, schema-valid payload mirroring ``_TEXT_FIXTURE_DIR``.

    Mirrors :func:`tests.extraction.test_schema_contract._valid_air_ticket_payload`
    deliberately — we want a payload we KNOW passes ``schema.validate`` so the
    success-case test is decoupled from any future schema tightening.
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
    tests assert against the dict — much friendlier to the type checker.
    """
    for record in caplog.records:
        if record.message == "extract_pdf":
            return {key: getattr(record, key) for key in _LOG_EXTRA_KEYS}
    msg = (
        "expected an 'extract_pdf' log record from extract_pdf, but only saw: "
        f"{[r.message for r in caplog.records]!r}"
    )
    raise AssertionError(msg)


# Tech-spec §2.7 — the fixed set of keys every ``extract_pdf`` log line emits.
# Keep in sync with :func:`where_tickets.extraction.extract._log_call`.
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


# --------------------------------------------------------------------------- #
# 1. Valid Haiku payload → returned with extraction_path="text"
# --------------------------------------------------------------------------- #


def test_haiku_returns_valid_payload(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """Happy path: valid emit_extracted_fields → tagged + returned."""
    pdf_path = _copy_fixture(tmp_path, _TEXT_FIXTURE_DIR / "document.pdf")
    payload = _valid_payload()
    fake = FakeBedrockExtractionClient(
        text_responses=[
            ToolUseResult(
                tool_name=TOOL_EMIT_EXTRACTED_FIELDS_NAME,
                tool_input=payload,
                usage=Usage(
                    input_tokens=100,
                    output_tokens=50,
                    cache_read_input_tokens=7,
                    cache_creation_input_tokens=3,
                ),
                latency_seconds=0.5,
            )
        ],
    )
    monkeypatch.setattr(extract, "_client_factory", lambda: fake)

    with caplog.at_level(logging.INFO, logger="where_tickets.extraction.extract"):
        result = extract_pdf(pdf_path)

    # Returned payload is the input + extraction_path tag. Project to a plain
    # dict for the assertions because ``extraction_path`` is a ``NotRequired``
    # field on the TypedDict and pyright won't let us subscript it directly.
    result_dict: dict[str, Any] = dict(result)
    assert result_dict["extraction_path"] == "text"
    assert result_dict["document_type"] == payload["document_type"]
    assert result_dict["cities"] == payload["cities"]
    assert result_dict["pdf_kind"] == "text"

    # Exactly one Haiku call with the expected shape.
    assert len(fake.text_calls) == 1
    call = fake.text_calls[0]
    assert call["model_alias"] == "haiku"
    assert call["tool_choice"] == {"type": "any"}
    tool_names = [tool["name"] for tool in call["tools"]]
    assert set(tool_names) == {
        TOOL_EMIT_EXTRACTED_FIELDS_NAME,
        TOOL_REPORT_NO_USEFUL_INFORMATION_NAME,
    }

    # Structured log line carries the §2.7 field set.
    extras = _find_log_extras(caplog)
    assert extras["extraction_path"] == "text"
    assert extras["model_path"] == "haiku-text"
    assert extras["sentinel_fired"] is False
    assert extras["error_reason"] is None
    assert extras["tokens_input"] == 100
    assert extras["tokens_output"] == 50
    assert extras["tokens_cache_read"] == 7
    assert extras["tokens_cache_creation"] == 3
    assert extras["pdf_page_count"] >= 1
    assert isinstance(extras["latency_ms_per_call"], list)
    assert len(extras["latency_ms_per_call"]) == 1


# Schema-fail / wrong-tool coverage now lives in ``test_extract_sonnet_fallback.py``
# (PATH C — Sonnet text fallback). The total-failure case (Haiku invalid → Sonnet
# invalid) lives in ``test_extract_failure.py``. Slice 7 replaced the
# "schema fail; sonnet fallback not implemented" placeholder with the real
# PATH C call, so the old placeholder-raise assertion that lived here has been
# moved into the new files where it belongs.
