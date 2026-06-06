"""Low-level PyMuPDF helpers for the extraction pipeline.

Two public functions, both deterministic and side-effect-free:

- :func:`extract_text` — concatenates every page's text layer. Returns the
  empty string when the stripped result falls below the
  ``_PYMUPDF_NULL_TEXT_FLOOR`` threshold, so callers can treat a stray page
  number on a rasterized PDF the same as a true empty text layer and route
  to the vision fallback.
- :func:`render_pages_to_jpeg` — one JPEG per page at ``_DPI`` / ``_JPEG_QUALITY``,
  matched to the corpus's ``pdf_kind: rasterized`` render settings so the
  extractor sees the same pixels the corpus produces.

``pymupdf`` is imported LAZILY inside each function so importing this module
works without the optional ``extraction`` dep group installed (the FastAPI
service image stays lean; ``pymupdf`` ships only with the Lambda image).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from pathlib import Path

__all__ = ["extract_text", "render_pages_to_jpeg"]

# Raster DPI for the vision path. Mirrors corpus/pdf/generator/render.py's
# ``_RASTER_DPI`` so the extractor sees the same pixels the rasterized corpus
# scenarios produce.
_DPI = 120

# JPEG encoder quality for the vision path. Balances file size against
# legibility of small ticket text (dates, station codes, QR payloads).
_JPEG_QUALITY = 80

# Minimum stripped-character count below which :func:`extract_text` returns
# the empty string. Rasterized PDFs can leak a handful of stray characters
# (page numbers, watermarks, the odd glyph outline) that we don't want to
# count as a real text layer; 8 chars is a conservative starting value
# (tune in code if the corpus surfaces a counter-example).
_PYMUPDF_NULL_TEXT_FLOOR = 8


def extract_text(pdf_path: Path) -> str:
    """Return the concatenated text of every page in ``pdf_path``.

    Pages are joined with ``\\n`` and returned verbatim (no whitespace
    normalisation) so the downstream model sees the original layout. If the
    stripped result is shorter than :data:`_PYMUPDF_NULL_TEXT_FLOOR`, returns
    the empty string — the signal that the caller should fall back to the
    vision path.
    """
    import pymupdf  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

    doc = pymupdf.open(str(pdf_path))
    try:
        text = "\n".join(page.get_text("text") for page in doc)
    finally:
        doc.close()
    if len(text.strip()) < _PYMUPDF_NULL_TEXT_FLOOR:
        return ""
    return text


def render_pages_to_jpeg(
    pdf_path: Path,
    *,
    dpi: int = _DPI,
    quality: int = _JPEG_QUALITY,
) -> list[bytes]:
    """Render every page of ``pdf_path`` to a JPEG byte string.

    Returns one ``bytes`` entry per page, in page order. Defaults match the
    corpus's ``pdf_kind: rasterized`` render settings (120 DPI, quality 80).
    """
    import pymupdf  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

    doc = pymupdf.open(str(pdf_path))
    try:
        return [
            page.get_pixmap(dpi=dpi).tobytes("jpeg", jpg_quality=quality)
            for page in doc
        ]
    finally:
        doc.close()
