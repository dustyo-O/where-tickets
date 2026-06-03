"""WeasyPrint orchestration for the PDF corpus generator.

``render_pdf(template_path, output_path, context)`` is the only public entry
point. It Jinja2-renders the named template with the given context, then hands
the HTML to WeasyPrint which writes a PDF to disk. With ``rasterized=True``
the WeasyPrint output is re-emitted as an image-only PDF (one PNG per page)
via PyMuPDF, dropping the text layer entirely so the corpus can deterministically
exercise the vision-fallback extraction path.

- The Jinja2 environment is rooted at ``templates/`` and uses
  ``FileSystemLoader`` so partials can be included via relative paths.
- WeasyPrint receives a ``base_url`` pointing at the same templates root so
  ``<link rel="stylesheet" href="styles/...">`` and ``@font-face`` ``src:
  url("fonts/...")`` declarations resolve to the bundled assets.
- Rasterization runs PyMuPDF at ``_RASTER_DPI`` per page (web-quality 120 DPI
  per tech-spec §2.3) and inserts each PNG into a fresh PDF page sized to the
  source page's mediabox, so the rasterized PDF has no extractable text.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pymupdf
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from weasyprint import HTML

# Templates root — both Jinja2 and WeasyPrint resolve relative paths against
# this. Stored as a module-level constant so ``render_pdf`` does not need it
# in its signature (the caller picks a *template name*, not a path).
TEMPLATES_ROOT: Path = Path(__file__).resolve().parent / "templates"

# DPI for rasterized renders. 120 DPI is the "web quality" target documented
# in technical-considerations §2.3: high enough that OCR/vision extraction
# remains realistic, low enough that committed PDF sizes stay reasonable.
_RASTER_DPI: int = 120


def _build_environment(templates_root: Path) -> Environment:
    """Build a Jinja2 environment rooted at ``templates_root``.

    ``StrictUndefined`` surfaces typos in template variables as render-time
    errors instead of silently inserting empty strings, which would defeat
    the PDF/JSON consistency check.
    """
    return Environment(
        loader=FileSystemLoader(str(templates_root)),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _rasterize_pdf_bytes(text_pdf_bytes: bytes, output_path: Path) -> None:
    """Re-emit ``text_pdf_bytes`` as an image-only PDF at ``_RASTER_DPI``.

    Opens the WeasyPrint output with PyMuPDF, rasterizes each page to a PNG,
    and writes a fresh PDF where every page is a single full-bleed image. The
    resulting file has no text layer — ``page.get_text("text")`` returns the
    empty string (modulo whitespace) for every page.
    """
    source = pymupdf.open(stream=text_pdf_bytes, filetype="pdf")
    try:
        target = pymupdf.open()
        try:
            for page in source:
                pixmap = page.get_pixmap(dpi=_RASTER_DPI)
                png_bytes = pixmap.tobytes("png")
                # Preserve the source page's dimensions so the visual layout
                # is preserved 1:1 — only the underlying representation
                # changes from glyph instructions to a bitmap.
                new_page = target.new_page(
                    width=page.rect.width,
                    height=page.rect.height,
                )
                new_page.insert_image(new_page.rect, stream=png_bytes)
            target.save(str(output_path))
        finally:
            target.close()
    finally:
        source.close()


def render_pdf(
    template_name: str,
    output_path: Path,
    *,
    context: dict[str, Any],
    templates_root: Path | None = None,
    rasterized: bool = False,
) -> None:
    """Render ``template_name`` with ``context`` and write a PDF to ``output_path``.

    Parameters
    ----------
    template_name:
        Template filename relative to ``templates_root`` (e.g.
        ``"air-ticket.html.j2"``).
    output_path:
        Filesystem path to write the resulting PDF to. Parent directory must
        exist (the CLI in ``__main__.py`` creates it).
    context:
        Template variables. Conventionally includes ``data`` (the
        ExtractedFields-shaped payload for the scenario), ``noise`` (a
        ``NoiseChoices``), and any pre-computed conveniences such as a
        ``legs`` list with formatted dates/times.
    templates_root:
        Override the default templates directory. Tests pass a tmpdir here.
    rasterized:
        When ``False`` (default), write the WeasyPrint text-layer PDF
        directly to ``output_path``. When ``True``, re-emit each WeasyPrint
        page as a 120-DPI PNG embedded in a fresh PDF — the result has no
        extractable text and matches ``pdf_kind: "rasterized"`` in the
        committed ``expected-fields.json``.
    """
    root = templates_root if templates_root is not None else TEMPLATES_ROOT
    environment = _build_environment(root)
    template = environment.get_template(template_name)
    html_content = template.render(**context)

    # `base_url` makes WeasyPrint resolve every relative URL in the HTML
    # (stylesheets, @font-face sources, etc.) against the templates root.
    html = HTML(string=html_content, base_url=str(root))

    if not rasterized:
        html.write_pdf(target=str(output_path))
        return

    # Rasterized path: render to bytes first, then re-emit via PyMuPDF.
    buffer = io.BytesIO()
    html.write_pdf(target=buffer)
    _rasterize_pdf_bytes(buffer.getvalue(), output_path)


__all__ = ["render_pdf", "TEMPLATES_ROOT"]
