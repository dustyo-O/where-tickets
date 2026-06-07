"""Unit tests for ``where_tickets.extraction.pdf`` helpers.

The committed Layer 1 corpus is the source of truth for both code paths:

- Text fixtures (``pdf_kind: "text"``) must yield a non-empty, multi-character
  text layer.
- Rasterized fixtures (``pdf_kind: "rasterized"``) must yield the empty string
  (so the extractor falls back to vision).
- JPEG rendering must produce one decodable JPEG per page; the multi-page
  scenario asserts the per-page count matches the PDF's ``page_count``.

The tests don't need network, AWS, or any state outside the committed corpus.
``pymupdf`` is in the ``extraction`` dep group; run via
``uv run --group extraction pytest tests/extraction/test_pdf_helpers.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# pymupdf ships only with the optional ``extraction`` dep group. ``just test``
# runs the full backend suite without that group, so skip the whole module
# when the package is absent — the dedicated `uv run --group extraction
# pytest tests/extraction` invocation still exercises these tests.
pymupdf = pytest.importorskip("pymupdf")

from where_tickets.extraction.pdf import (  # noqa: E402 — importorskip gate
    _PYMUPDF_NULL_TEXT_FLOOR,
    extract_text,
    render_pages_to_jpeg,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIOS_DIR = REPO_ROOT / "corpus" / "pdf" / "layer1" / "scenarios"

# JPEG SOI (Start Of Image) marker — every well-formed JPEG starts with this.
_JPEG_MAGIC = b"\xff\xd8\xff"


def _scenarios_by_kind() -> dict[str, list[Path]]:
    """Group every Layer 1 scenario by its declared ``pdf_kind``."""
    grouped: dict[str, list[Path]] = {}
    for scenario_dir in sorted(SCENARIOS_DIR.iterdir()):
        if not scenario_dir.is_dir():
            continue
        json_path = scenario_dir / "expected-fields.json"
        if not json_path.exists():
            continue
        payload = json.loads(json_path.read_text())
        kind = payload.get("pdf_kind")
        if not isinstance(kind, str):
            continue
        grouped.setdefault(kind, []).append(scenario_dir / "document.pdf")
    return grouped


def _first_multi_page(pdfs: list[Path]) -> tuple[Path, int] | None:
    """Return the first ``(pdf_path, page_count)`` with > 1 page, or None."""
    for pdf_path in pdfs:
        doc = pymupdf.open(str(pdf_path))
        try:
            count = doc.page_count
        finally:
            doc.close()
        if count > 1:
            return pdf_path, count
    return None


def test_extract_text_returns_non_empty_on_text_pdf() -> None:
    """A ``pdf_kind: text`` fixture must yield real, sizeable text."""
    text_pdfs = _scenarios_by_kind().get("text", [])
    assert text_pdfs, "corpus must contain at least one pdf_kind=text scenario"

    text = extract_text(text_pdfs[0])

    stripped = text.strip()
    # Strictly above the floor — make sure a barely-passing rasterized PDF
    # could not masquerade as a text PDF here.
    assert len(stripped) >= 50, (
        f"text PDF {text_pdfs[0].name} yielded only {len(stripped)} stripped chars"
    )


def test_extract_text_returns_empty_on_rasterized_pdf() -> None:
    """A ``pdf_kind: rasterized`` fixture must yield the empty string."""
    rasterized_pdfs = _scenarios_by_kind().get("rasterized", [])
    assert rasterized_pdfs, (
        "corpus must contain at least one pdf_kind=rasterized scenario"
    )

    text = extract_text(rasterized_pdfs[0])

    assert text == "", (
        f"rasterized PDF {rasterized_pdfs[0].name} leaked text: {text[:80]!r}"
    )


def test_null_text_floor_is_documented_constant() -> None:
    """Guard against accidental tuning: the floor should stay a small int."""
    assert isinstance(_PYMUPDF_NULL_TEXT_FLOOR, int)
    assert 1 <= _PYMUPDF_NULL_TEXT_FLOOR <= 64


def test_render_pages_to_jpeg_returns_one_decodable_jpeg_per_page() -> None:
    """Every entry must be JPEG-magic-prefixed bytes that round-trip via PyMuPDF."""
    grouped = _scenarios_by_kind()
    any_pdfs = [pdf for pdfs in grouped.values() for pdf in pdfs]
    assert any_pdfs, "corpus is empty"

    pdf_path = any_pdfs[0]
    pages = render_pages_to_jpeg(pdf_path)

    doc = pymupdf.open(str(pdf_path))
    try:
        expected_count = doc.page_count
    finally:
        doc.close()

    assert len(pages) == expected_count >= 1
    for index, page_bytes in enumerate(pages):
        assert isinstance(page_bytes, bytes)
        assert page_bytes.startswith(_JPEG_MAGIC), (
            f"page {index} of {pdf_path.name} missing JPEG SOI marker"
        )
        # Round-trip: PyMuPDF should be able to open the JPEG as a Pixmap.
        pix = pymupdf.Pixmap(page_bytes)
        assert pix.width > 0 and pix.height > 0


def test_render_pages_to_jpeg_matches_page_count_on_multi_page_pdf() -> None:
    """The byte-list length must match ``page_count`` on a > 1 page PDF."""
    grouped = _scenarios_by_kind()
    candidates = [pdf for pdfs in grouped.values() for pdf in pdfs]
    multi = _first_multi_page(candidates)
    if multi is None:
        pytest.skip("corpus has no multi-page scenarios")

    pdf_path, page_count = multi
    pages = render_pages_to_jpeg(pdf_path)

    assert len(pages) == page_count
    assert page_count > 1  # sanity: we picked a genuinely multi-page PDF
