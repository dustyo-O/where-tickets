"""Slice 1 smoke test for the PDF extractor stub.

The extractor will not have a real implementation until Slice 5+. This test
just asserts that:

1. The public symbols can be imported from ``where_tickets.extraction``.
2. Calling :func:`extract_pdf` on a real PDF raises :class:`ExtractionFailedError`
   with the canonical ``"not implemented yet"`` message.

A committed Layer 1 corpus PDF is copied into ``tmp_path`` rather than built
with PyMuPDF, because PyMuPDF lives in the optional ``extraction`` /
``corpus`` dep groups — the default ``dev`` group must not require it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from where_tickets.extraction import ExtractionFailedError, extract_pdf


REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PDF = (
    REPO_ROOT
    / "corpus"
    / "pdf"
    / "layer1"
    / "scenarios"
    / "001-air-1leg-1pax-paris-lisbon"
    / "document.pdf"
)


def test_extract_pdf_stub_raises_not_implemented(tmp_path: Path) -> None:
    """Stub extractor raises ExtractionFailedError on any real PDF input."""
    pdf_path = tmp_path / "document.pdf"
    shutil.copy(FIXTURE_PDF, pdf_path)

    with pytest.raises(ExtractionFailedError, match="not implemented yet"):
        extract_pdf(pdf_path)
