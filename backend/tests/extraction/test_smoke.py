"""Smoke test for the PDF extractor public surface.

Asserts that:

1. The public symbols can be imported from ``where_tickets.extraction``.
2. Calling :func:`extract_pdf` on a rasterized fixture takes the empty-text
   short-circuit and raises :class:`ExtractionFailedError` with the
   Slice 5 placeholder message — proving the wiring lands without touching
   live Bedrock at all (the vision path lands in Slice 6).

A committed Layer 1 rasterized PDF is copied into ``tmp_path``; we
deliberately pick the rasterized kind so :func:`pdf.extract_text` returns
the empty string and short-circuits before the Bedrock factory is touched.
That keeps this test offline and dep-light: no ``--group extraction`` needed.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

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


def test_extract_pdf_raises_on_empty_text_fixture(tmp_path: Path) -> None:
    """A rasterized PDF short-circuits to the Slice 5 placeholder raise.

    Slice 6 replaces this branch with the real vision path; until then, the
    placeholder reason proves Path A's empty-text check is wired correctly.
    """
    pdf_path = tmp_path / "document.pdf"
    shutil.copy(_first_rasterized_pdf(), pdf_path)

    with pytest.raises(
        ExtractionFailedError, match=r"vision path not implemented"
    ):
        extract_pdf(pdf_path)
