"""Integration runner: live extractor -> adapter -> engine -> scoring.

DUS-31 Slice 7. Drives the per-trip pipeline end-to-end:

1. Discover trips from ``corpus/integration/<slug>/{manifest.json,
   expected-route.json}``.
2. For each trip, in manifest order, call the production extractor on every
   referenced PDF (under ``corpus/pdf/layer1/scenarios/``), feed the result
   through :func:`spikes.integration.adapter.extracted_fields_to_fragment`,
   then fold the fragment into a running :class:`WorkingRoute` via
   :func:`spikes.route_engine_algorithmic.engine.update_route`.
3. Assert the final route against the trip's ``expected-route.json`` via
   :func:`spikes.route_engine_llm.scoring.final_route_match` (unless
   ``--no-route-check`` is set).
4. Print a per-trip PASS/FAIL block + a final ``N/M trips PASS`` line, and
   (optionally) write a JSON report to ``--json-report``.

The extractor is loaded LAZILY via :func:`_load_extractor` — the dotted import
path is configurable via ``--extractor-import-path`` (defaults to
``where_tickets.extraction.extract_pdf``). Tests pass an in-process stub via
``run_trip(extractor=...)`` and never load the production module, so the
persistent backend venv stays clean of ``anthropic`` (memory:
``project_extraction_isolated_venv``).

Exit codes mirror ``corpus/pdf/runner.py``:

- ``0`` — every discovered trip PASSed.
- ``1`` — at least one trip FAILed.
- ``2`` — top-level error.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from spikes.integration.adapter import (
    AdapterError,
    ExtractedFields,
    extracted_fields_to_fragment,
)
from spikes.integration.report import (
    DocumentOutcome,
    TripResult,
    build_report_dict,
    render_run_summary,
    render_trip_summary,
)
from spikes.route_engine_algorithmic.engine import EngineError, update_route
from spikes.route_engine_llm.corpus import ExpectedRoute
from spikes.route_engine_llm.models import WorkingRoute
from spikes.route_engine_llm.scoring import final_route_match

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

__all__ = [
    "Extractor",
    "Manifest",
    "ManifestDocument",
    "Trip",
    "build_parser",
    "discover",
    "main",
    "run_trip",
    "run_trips",
]


# --------------------------------------------------------------------------- #
# Paths (mirrors corpus/pdf/runner.py's repo-root resolution)
# --------------------------------------------------------------------------- #


# This file: backend/spikes/integration/runner.py
#   parents[0] = backend/spikes/integration/
#   parents[1] = backend/spikes/
#   parents[2] = backend/
#   parents[3] = <repo root>
_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[3]


def _repo_root() -> Path:
    """Locate the repo root, honouring ``WT_REPO_ROOT`` for tests."""
    override = os.environ.get("WT_REPO_ROOT")
    return Path(override).resolve() if override else _DEFAULT_REPO_ROOT


def _default_integration_root() -> Path:
    return _repo_root() / "corpus" / "integration"


def _default_layer1_root() -> Path:
    return _repo_root() / "corpus" / "pdf" / "layer1" / "scenarios"


# --------------------------------------------------------------------------- #
# Manifest + Trip models
# --------------------------------------------------------------------------- #


class ManifestDocument(BaseModel):
    """One document entry inside a trip's ``manifest.json``.

    ``pdf`` is the path relative to ``--layer1-root`` (default
    ``corpus/pdf/layer1/scenarios/``). ``expect_unreadable`` flags a PDF the
    runner should accept as a failed extraction without failing the trip —
    exercises spec 007 §2.6.
    """

    model_config = ConfigDict(extra="forbid")

    pdf: str
    expect_unreadable: bool = False


class Manifest(BaseModel):
    """A trip's ``manifest.json`` payload (see ``corpus/integration/README.md``)."""

    model_config = ConfigDict(extra="forbid")

    travelers: list[str] = Field(min_length=1)
    documents: list[ManifestDocument] = Field(min_length=1)
    notes: str | None = None


@dataclass(slots=True)
class Trip:
    """A discovered integration trip ready to be replayed.

    ``manifest`` is the parsed payload; ``expected_route_path`` is the file
    path to ``expected-route.json`` (loaded lazily by :func:`run_trip` so
    discovery failures surface separately from scoring failures).
    """

    slug: str
    directory: Path
    manifest: Manifest
    expected_route_path: Path


# --------------------------------------------------------------------------- #
# Extractor seam
# --------------------------------------------------------------------------- #


type Extractor = Callable[[Path], ExtractedFields]


def _load_extractor(dotted_path: str) -> Extractor | None:
    """Import the extractor callable by dotted path; return None if unavailable.

    Mirrors :func:`corpus.pdf.runner._load_extractor` so the persistent
    backend venv never imports ``where_tickets.extraction.*`` at module
    top — the import only happens here, after argument parsing.
    """
    if "." not in dotted_path:
        return None
    module_path, _, attr = dotted_path.rpartition(".")
    try:
        module = importlib.import_module(module_path)
        candidate = getattr(module, attr)
    except (ImportError, AttributeError):
        return None
    if not callable(candidate):
        return None
    return cast("Extractor", candidate)


def _load_extraction_failed_error() -> type[BaseException]:
    """Return ``ExtractionFailedError`` if importable, else a sentinel base.

    Used by :func:`run_trip` to ``except`` only the extractor's documented
    failure type. When the production extractor isn't installed (e.g. in
    the persistent backend venv during tests), we fall back to a dummy class
    that nothing will ever raise, so :func:`run_trip` correctly bubbles
    everything else as an unexpected error.
    """
    try:
        module = importlib.import_module("where_tickets.extraction")
    except ImportError:
        return _NeverRaised
    candidate = getattr(module, "ExtractionFailedError", None)
    if isinstance(candidate, type) and issubclass(candidate, BaseException):
        return candidate
    return _NeverRaised


class _NeverRaised(Exception):
    """Sentinel exception type; nothing in the codebase raises it.

    Used by :func:`_load_extraction_failed_error` when the production
    extractor module is not importable, so the ``except`` clause in
    :func:`run_trip` is well-typed without ever firing.
    """


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _DiscoveryError:
    """A trip directory that couldn't be turned into a :class:`Trip`."""

    slug: str
    directory: Path
    reason: str


def discover(
    integration_root: Path,
) -> tuple[list[Trip], list[_DiscoveryError]]:
    """Walk ``integration_root`` for trip directories.

    Each child directory must contain ``manifest.json`` AND
    ``expected-route.json``. Missing files / malformed manifests surface as
    :class:`_DiscoveryError` entries (the runner reports them as failed trips
    so silent skips are impossible).
    """
    if not integration_root.is_dir():
        return [], []

    trips: list[Trip] = []
    errors: list[_DiscoveryError] = []
    for entry in sorted(p for p in integration_root.iterdir() if p.is_dir()):
        slug = entry.name
        manifest_path = entry / "manifest.json"
        expected_route_path = entry / "expected-route.json"
        if not manifest_path.is_file():
            errors.append(
                _DiscoveryError(
                    slug=slug,
                    directory=entry,
                    reason=f"missing manifest.json under {entry}",
                )
            )
            continue
        if not expected_route_path.is_file():
            errors.append(
                _DiscoveryError(
                    slug=slug,
                    directory=entry,
                    reason=f"missing expected-route.json under {entry}",
                )
            )
            continue
        try:
            manifest = Manifest.model_validate_json(manifest_path.read_text("utf-8"))
        except ValidationError as exc:
            errors.append(
                _DiscoveryError(
                    slug=slug,
                    directory=entry,
                    reason=f"manifest.json failed validation: {exc}",
                )
            )
            continue
        trips.append(
            Trip(
                slug=slug,
                directory=entry,
                manifest=manifest,
                expected_route_path=expected_route_path,
            )
        )
    return trips, errors


# --------------------------------------------------------------------------- #
# Per-trip execution
# --------------------------------------------------------------------------- #


def run_trip(
    trip: Trip,
    *,
    extractor: Extractor,
    layer1_root: Path,
    route_check: bool = True,
    extraction_failed_error: type[BaseException] | None = None,
) -> TripResult:
    """Extract -> adapt -> fold every manifest document; score the final route.

    Per-document, an extraction failure marks the document FAILed unless
    ``expect_unreadable: true`` (in which case the document is skipped without
    failing the trip). An adapter or engine error marks the trip FAILed but
    the loop continues so every document's outcome is surfaced.

    When ``route_check`` is False, the trip PASSes if no per-document errors
    were recorded. Otherwise the final :class:`WorkingRoute` is matched
    against the trip's ``expected-route.json`` via
    :func:`final_route_match`; the trip FAILs on a scoring mismatch with the
    ``CheckResult.reason`` captured for the report.
    """
    extraction_failed_error = (
        extraction_failed_error
        if extraction_failed_error is not None
        else _load_extraction_failed_error()
    )

    started = time.perf_counter()
    result = TripResult(slug=trip.slug, skipped_route_check=not route_check)
    working = WorkingRoute()

    for doc in trip.manifest.documents:
        outcome = DocumentOutcome(pdf=doc.pdf)
        result.documents.append(outcome)
        pdf_path = layer1_root / doc.pdf

        # --- 1. Extract -------------------------------------------------- #
        extract_started = time.perf_counter()
        try:
            fields = extractor(pdf_path)
        except extraction_failed_error as exc:  # type: ignore[misc]
            outcome.latency_ms = (time.perf_counter() - extract_started) * 1000
            if doc.expect_unreadable:
                outcome.error = f"extraction failed (expected): {exc}"
                continue
            outcome.error = f"extraction failed (not flagged expect_unreadable): {exc}"
            result.failures.append(outcome.error)
            continue
        except Exception as exc:  # noqa: BLE001 — surface as a per-doc failure
            outcome.latency_ms = (time.perf_counter() - extract_started) * 1000
            outcome.error = f"unexpected extractor error: {type(exc).__name__}: {exc}"
            result.failures.append(outcome.error)
            continue
        outcome.latency_ms = (time.perf_counter() - extract_started) * 1000
        outcome.extracted = True
        outcome.pdf_kind = _safe_str(fields.get("pdf_kind"))
        outcome.extraction_path = _safe_str(fields.get("extraction_path"))

        # --- 2. Adapt ---------------------------------------------------- #
        try:
            fragment = extracted_fields_to_fragment(
                fields, source_document_id=doc.pdf
            )
        except AdapterError as exc:
            outcome.error = f"adapter error: {exc}"
            result.failures.append(outcome.error)
            continue
        outcome.adapted = True

        # --- 3. Fold into engine ---------------------------------------- #
        try:
            update_route(working, fragment)
        except EngineError as exc:
            outcome.error = f"engine error: {exc}"
            result.failures.append(outcome.error)
            continue
        outcome.folded = True

    # --- 4. Score (or short-circuit on --no-route-check) ---------------- #
    if not route_check:
        result.passed = not result.failures
    else:
        try:
            expected = ExpectedRoute.model_validate_json(
                trip.expected_route_path.read_text("utf-8")
            )
        except (OSError, ValidationError) as exc:
            reason = f"expected-route.json could not be loaded: {exc}"
            result.failures.append(reason)
            result.scoring_reason = reason
            result.passed = False
        else:
            check = final_route_match(working, expected)
            if check.passed and not result.failures:
                result.passed = True
            else:
                if not check.passed:
                    result.scoring_reason = check.reason
                    if check.reason is not None:
                        result.failures.append(f"scoring: {check.reason}")
                result.passed = False

    result.latency_ms_total = (time.perf_counter() - started) * 1000
    return result


def _safe_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def run_trips(
    trips: list[Trip],
    *,
    extractor: Extractor,
    layer1_root: Path,
    route_check: bool = True,
    extraction_failed_error: type[BaseException] | None = None,
) -> list[TripResult]:
    """Run :func:`run_trip` over each trip; return one :class:`TripResult` per."""
    return [
        run_trip(
            trip,
            extractor=extractor,
            layer1_root=layer1_root,
            route_check=route_check,
            extraction_failed_error=extraction_failed_error,
        )
        for trip in trips
    ]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser. Exposed for tests."""
    parser = argparse.ArgumentParser(
        description=(
            "Drive integration trips end-to-end: extract -> adapter -> "
            "engine -> scoring. Live Bedrock per PDF."
        ),
    )
    parser.add_argument(
        "--trip",
        action="append",
        default=None,
        help=(
            "Run only trips with the matching slug (repeatable). "
            "Default: every trip under --integration-root."
        ),
    )
    parser.add_argument(
        "--no-route-check",
        action="store_true",
        help=(
            "Skip the expected-route.json scoring step. A trip PASSes when "
            "extraction + adapter + engine all succeed."
        ),
    )
    parser.add_argument(
        "--json-report",
        default=None,
        help=(
            "If set, also write a machine-readable JSON report at this path."
        ),
    )
    parser.add_argument(
        "--extractor-import-path",
        default="where_tickets.extraction.extract_pdf",
        help=(
            "Dotted import path of the extractor callable "
            "(default: where_tickets.extraction.extract_pdf)."
        ),
    )
    parser.add_argument(
        "--integration-root",
        default=None,
        help=(
            "Override the corpus/integration root (default: "
            "<repo>/corpus/integration)."
        ),
    )
    parser.add_argument(
        "--layer1-root",
        default=None,
        help=(
            "Override the layer-1 PDF root used to resolve manifest "
            "documents (default: <repo>/corpus/pdf/layer1/scenarios)."
        ),
    )
    return parser


def _filter_trips(trips: list[Trip], wanted: Sequence[str] | None) -> list[Trip]:
    if not wanted:
        return trips
    wanted_set = set(wanted)
    return [t for t in trips if t.slug in wanted_set]


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Exit codes mirror :mod:`corpus.pdf.runner` — 0 / 1 / 2 for all-pass /
    any-fail / top-level error.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        integration_root = (
            Path(args.integration_root).resolve()
            if args.integration_root is not None
            else _default_integration_root()
        )
        layer1_root = (
            Path(args.layer1_root).resolve()
            if args.layer1_root is not None
            else _default_layer1_root()
        )

        trips, discovery_errors = discover(integration_root)
        if discovery_errors:
            for err in discovery_errors:
                print(f"FAIL  {err.slug}: {err.reason}")

        trips = _filter_trips(trips, args.trip)
        if not trips and not discovery_errors:
            print(f"no trips discovered under {integration_root}")
            return 1

        extractor = _load_extractor(args.extractor_import_path)
        if extractor is None:
            print(
                f"extractor not importable at {args.extractor_import_path!r}; "
                "run under the extraction venv (e.g. `just integration ...`)."
            )
            return 2

        results = run_trips(
            trips,
            extractor=extractor,
            layer1_root=layer1_root,
            route_check=not args.no_route_check,
        )

        for result in results:
            print(render_trip_summary(result))
            print()

        # Report
        print(render_run_summary(results))
        if args.json_report:
            payload = build_report_dict(results)
            Path(args.json_report).write_text(
                json.dumps(payload, indent=2, sort_keys=True, default=str)
            )

        if discovery_errors:
            return 1
        return 1 if any(not r.passed for r in results) else 0
    except Exception:  # noqa: BLE001 — top-level safety net
        traceback.print_exc()
        return 2


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
