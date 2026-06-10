"""CLI: ``python -m corpus.integration.generator``.

Emits every catalogued integration trip — PDFs under
``corpus/pdf/layer2/<slug>/`` and the trip's ``manifest.json`` +
``expected-route.json`` under ``corpus/integration/<slug>/``. Per-PDF expected
fields land alongside the PDFs so the layer-2 PDF runner discovers them for
free.

Cleans only the trip slugs it regenerates — other contents of
``corpus/pdf/layer2/`` and ``corpus/integration/`` are left in place. Use
``--trip <slug>`` to regenerate a single trip during iteration.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from corpus.integration.generator.catalog import all_trips
from corpus.integration.generator.composer import (
    ComposerError,
    PDFEntry,
    TripBundle,
    TripSpec,
    compose_trip,
)


REPO_ROOT: Path = Path(__file__).resolve().parents[3]
DEFAULT_PDF_OUTPUT_DIR: Path = REPO_ROOT / "corpus" / "pdf" / "layer2"
DEFAULT_INTEGRATION_OUTPUT_DIR: Path = REPO_ROOT / "corpus" / "integration"


def _wipe(path: Path) -> None:
    """Recursively delete ``path`` if it exists. Used to clean a trip slug."""
    if not path.exists():
        return
    if path.is_dir():
        for child in path.iterdir():
            _wipe(child)
        path.rmdir()
    else:
        path.unlink()


def _write_pdf(
    entry: PDFEntry,
    *,
    output_path: Path,
) -> None:
    """Render or fabricate one PDF and write it to ``output_path``."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if entry.expect_unreadable:
        _write_blank_pdf(output_path)
        return

    assert entry.render_template is not None  # noqa: S101 — composer guarantees this
    assert entry.render_context is not None  # noqa: S101
    from corpus.pdf.generator.render import render_pdf  # noqa: PLC0415 — corpus-venv-only import

    render_pdf(
        entry.render_template,
        output_path,
        context=entry.render_context,
        rasterized=entry.rendering == "rasterized",
    )


def _write_blank_pdf(output_path: Path) -> None:
    """Write a minimal valid PDF with no extractable text.

    Used for ``expect_unreadable`` primitives. The PDF is a single blank
    A4 page — PyMuPDF parses it cleanly, but the production extractor's
    vision path (and the text path) return empty content, which surfaces
    as :class:`ExtractionFailedError` in :mod:`where_tickets.extraction`.
    """
    import pymupdf  # noqa: PLC0415 — corpus dep group only

    doc = pymupdf.open()
    try:
        # A4 in points: 595 x 842 — matches WeasyPrint's default page size.
        doc.new_page(width=595, height=842)
        doc.save(str(output_path))
    finally:
        doc.close()


def _write_expected_fields(
    entry: PDFEntry,
    *,
    output_path: Path,
) -> None:
    """Write the sibling ``<NN>-<docname>.expected-fields.json`` for ``entry``."""
    if entry.expect_unreadable or entry.expected_fields is None:
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(entry.expected_fields, indent=2, sort_keys=True) + "\n"
    )


def _emit_trip(
    bundle: TripBundle,
    *,
    pdf_root: Path,
    integration_root: Path,
) -> None:
    """Write every artefact for one trip to disk.

    Wipes only this trip's slug under each output dir before re-emitting; other
    trips and any hand-crafted content under ``corpus/pdf/layer2/`` or
    ``corpus/integration/`` are preserved.
    """
    trip_pdf_dir = pdf_root / bundle.slug
    trip_integration_dir = integration_root / bundle.slug
    _wipe(trip_pdf_dir)
    _wipe(trip_integration_dir)

    for entry in bundle.pdfs:
        pdf_target = pdf_root / entry.relpath
        _write_pdf(entry, output_path=pdf_target)
        expected_fields_target = pdf_target.with_suffix(".expected-fields.json")
        _write_expected_fields(entry, output_path=expected_fields_target)

    trip_integration_dir.mkdir(parents=True, exist_ok=True)
    (trip_integration_dir / "manifest.json").write_text(
        json.dumps(bundle.manifest, indent=2, sort_keys=True) + "\n"
    )
    (trip_integration_dir / "expected-route.json").write_text(
        json.dumps(bundle.expected_route, indent=2, sort_keys=True) + "\n"
    )


def run(
    *,
    trip_slugs: list[str] | None,
    pdf_root: Path,
    integration_root: Path,
) -> int:
    """Emit every selected trip; return a process exit code (0 == success)."""
    specs: list[TripSpec] = list(all_trips())
    if trip_slugs:
        wanted = set(trip_slugs)
        unknown = wanted - {s.slug for s in specs}
        if unknown:
            for slug in sorted(unknown):
                print(f"error: no such trip in catalog: {slug}", file=sys.stderr)
            return 2
        specs = [s for s in specs if s.slug in wanted]

    for spec in specs:
        try:
            bundle = compose_trip(spec)
        except ComposerError as exc:
            print(f"error: composer failed for {spec.slug}: {exc}", file=sys.stderr)
            return 2
        _emit_trip(
            bundle,
            pdf_root=pdf_root,
            integration_root=integration_root,
        )
        print(f"wrote trip {spec.slug} ({len(bundle.pdfs)} PDFs)")
    print(f"Generated {len(specs)} trip(s).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trip",
        action="append",
        default=None,
        help="Only regenerate the named trip (slug). Repeatable.",
    )
    parser.add_argument(
        "--output-dir-pdf",
        type=Path,
        default=DEFAULT_PDF_OUTPUT_DIR,
        help=f"Where to write layer-2 PDFs (default: {DEFAULT_PDF_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--output-dir-integration",
        type=Path,
        default=DEFAULT_INTEGRATION_OUTPUT_DIR,
        help=(
            "Where to write integration trip dirs "
            f"(default: {DEFAULT_INTEGRATION_OUTPUT_DIR})."
        ),
    )
    args = parser.parse_args(argv)
    return run(
        trip_slugs=args.trip,
        pdf_root=args.output_dir_pdf.resolve(),
        integration_root=args.output_dir_integration.resolve(),
    )


if __name__ == "__main__":
    sys.exit(main())
