"""``python -m where_tickets.extraction <pdf-path>`` — one-PDF debug runner.

Wraps :func:`where_tickets.extraction.extract_pdf` so an engineer can inspect
a single scenario on demand without re-running the full corpus. Prints the
resulting :class:`~where_tickets.extraction.ExtractedFields` as pretty JSON on
stdout; surfaces the chosen ``extraction_path`` + ``model_path`` (read from
the structured ``"extract_pdf"`` log record emitted by :func:`extract_pdf`)
on stderr. Exits 0 on success, 1 on :class:`ExtractionFailedError`, 2 on
usage / file-not-found.

Design notes:

- The result JSON goes to **stdout** and diagnostics go to **stderr** — strict
  separation, so the user can pipe stdout into ``jq`` without diagnostics
  polluting the stream.
- The structured log record (see ``extract._log_call``) is the public
  contract for ``model_path`` — the field is not present on the returned
  payload. The CLI installs a tiny :class:`logging.Handler` that captures
  exactly that one record so the diagnostic line can read both
  ``extraction_path`` and ``model_path`` without rummaging in extractor
  internals. The handler is removed in a ``finally`` block so repeated calls
  (notably in tests) don't leak.
- :data:`where_tickets.extraction.extract._client_factory` is the test seam.
  Production callers get the default :func:`bedrock.make_client`; CLI tests
  swap it via ``monkeypatch.setattr`` to inject a fake client.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import IO

from where_tickets.extraction.extract import ExtractionFailedError, extract_pdf

__all__ = ["main"]


# The logger name :func:`extract_pdf` uses for its single structured record —
# kept as a module-level constant so the CLI and the extractor agree on which
# logger to wire the capture handler to.
_EXTRACT_LOGGER_NAME = "where_tickets.extraction.extract"


class _LogCapture(logging.Handler):
    """Capture the one ``"extract_pdf"`` structured log record per call.

    Lets the CLI surface ``model_path`` and ``extraction_path`` on stderr
    without reaching into the extractor's internals — the log record IS the
    documented public contract for those fields (per tech-spec §2.7).
    """

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.record: logging.LogRecord | None = None

    def emit(self, record: logging.LogRecord) -> None:
        # ``record.message`` is only populated by ``getMessage()``; compare
        # against ``record.msg`` (the raw format string) instead so we don't
        # depend on the handler chain having already formatted the record.
        if record.name == _EXTRACT_LOGGER_NAME and record.msg == "extract_pdf":
            self.record = record


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m where_tickets.extraction",
        description=(
            "Extract structured fields from a single PDF using the "
            "production extractor against live Bedrock. Pretty JSON goes "
            "to stdout; diagnostics go to stderr."
        ),
    )
    parser.add_argument("pdf_path", type=Path, help="Path to the PDF file.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code.

    Exit codes:

    - ``0`` — success; pretty JSON of the :class:`ExtractedFields` is on stdout.
    - ``1`` — :class:`ExtractionFailedError`; reason printed on stderr.
    - ``2`` — usage error or PDF path does not exist.
    """
    args = _build_parser().parse_args(argv)
    pdf_path: Path = args.pdf_path

    if not pdf_path.exists():
        print(f"error: PDF not found: {pdf_path}", file=sys.stderr)
        return 2

    capture = _LogCapture()
    extract_logger = logging.getLogger(_EXTRACT_LOGGER_NAME)
    extract_logger.addHandler(capture)
    # `extract_pdf` logs at INFO. If the caller never configured a level,
    # ``NOTSET`` (0) inherits root's default of WARNING and our record gets
    # filtered out — bump the logger to INFO so the capture handler sees the
    # record. Restore the prior level in ``finally`` so we don't permanently
    # mutate global logging state across repeated CLI calls (e.g. in tests).
    previous_level = extract_logger.level
    if previous_level == logging.NOTSET or previous_level > logging.INFO:
        extract_logger.setLevel(logging.INFO)

    try:
        try:
            result = extract_pdf(pdf_path)
        except ExtractionFailedError as exc:
            print(f"ExtractionFailedError: {exc}", file=sys.stderr)
            _print_diagnostics(capture.record, sys.stderr)
            return 1

        json.dump(result, sys.stdout, indent=2, sort_keys=True, default=str)
        sys.stdout.write("\n")
        _print_diagnostics(capture.record, sys.stderr)
        return 0
    finally:
        extract_logger.removeHandler(capture)
        extract_logger.setLevel(previous_level)


def _print_diagnostics(record: logging.LogRecord | None, stream: IO[str]) -> None:
    """Print the captured log record's path diagnostics on ``stream``.

    Silently no-ops if no record was captured — defensive against future
    refactors that might skip the log call (e.g. a hypothetical
    ``--quiet`` mode); current code paths in :func:`extract_pdf` always
    emit the record, success or failure.
    """
    if record is None:
        return
    extraction_path = getattr(record, "extraction_path", None)
    model_path = getattr(record, "model_path", None)
    print(
        f"extraction_path={extraction_path!s}  model_path={model_path!s}",
        file=stream,
    )


if __name__ == "__main__":  # pragma: no cover — exercised via __main__.py
    sys.exit(main())
