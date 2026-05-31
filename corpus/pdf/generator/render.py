"""WeasyPrint orchestration for the PDF corpus generator.

``render_pdf(template_path, output_path, context)`` is the only public entry
point. It Jinja2-renders the named template with the given context, then hands
the HTML to WeasyPrint which writes a real text-layer PDF to disk.

Slice 3 scope:

- Text-layer PDFs only (``pdf_kind == "text"``). The rasterized branch lands
  in Slice 5.
- The Jinja2 environment is rooted at ``templates/`` and uses
  ``FileSystemLoader`` so partials can be included via relative paths.
- WeasyPrint receives a ``base_url`` pointing at the same templates root so
  ``<link rel="stylesheet" href="styles/...">`` and ``@font-face`` ``src:
  url("fonts/...")`` declarations resolve to the bundled assets.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from weasyprint import HTML

# Templates root — both Jinja2 and WeasyPrint resolve relative paths against
# this. Stored as a module-level constant so ``render_pdf`` does not need it
# in its signature (the caller picks a *template name*, not a path).
TEMPLATES_ROOT: Path = Path(__file__).resolve().parent / "templates"


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


def render_pdf(
    template_name: str,
    output_path: Path,
    *,
    context: dict[str, Any],
    templates_root: Path | None = None,
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
    """
    root = templates_root if templates_root is not None else TEMPLATES_ROOT
    environment = _build_environment(root)
    template = environment.get_template(template_name)
    html_content = template.render(**context)

    # `base_url` makes WeasyPrint resolve every relative URL in the HTML
    # (stylesheets, @font-face sources, etc.) against the templates root.
    html = HTML(string=html_content, base_url=str(root))
    html.write_pdf(target=str(output_path))


__all__ = ["render_pdf", "TEMPLATES_ROOT"]
