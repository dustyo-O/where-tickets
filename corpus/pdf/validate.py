"""Per-PDF structural + drift + token-sanity validation for the corpus.

Runs six checks for each committed scenario (technical-considerations §2.7):

1. Schema (Draft 2020-12) against ``schema/expected-fields.schema.json``.
2. City-integrity: every ``stations[]/accommodations[]/venues[].city`` value
   must appear in the top-level ``cities[]``.
3. Per-``document_type`` minimum counts (air/rail/bus -> stations>=2; hotel/
   airbnb -> accommodations>=1; supplementary -> none).
4. Datetime-presence on transit stations (each station carries departure or
   arrival).
5. **JSON drift (Layer 1 only):** enumerate the generator matrix, serialize
   each ``ScenarioSpec.expected_fields()`` exactly like the generator does
   (``indent=2, sort_keys=True`` + trailing newline) and byte-compare; orphan
   or missing directories also fail. Skipped with a warning if the matrix
   module can't be imported.
6. **PDF/JSON token sanity:** PyMuPDF reads the sibling PDF; for
   ``pdf_kind=="text"`` every ``cities[]`` value and every distinct
   ``YYYY-MM-DD`` date prefix from any ``*_datetime`` field must appear in
   the extracted text. ``pdf_kind=="rasterized"`` asserts the text layer is
   empty. Skipped with a warning if pymupdf isn't importable.

Invocation::

    uv run --python 3.12 --with jsonschema --with pymupdf python corpus/pdf/validate.py

Exit codes: 0 = all-pass, 1 = any failure, 2 = unexpected fatal error.
"""

from __future__ import annotations

import difflib
import json
import sys
from pathlib import Path
from typing import Any

try:
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover - guidance for direct invocation
    print(
        "ERROR: jsonschema is not installed. Run via: "
        "uv run --python 3.12 --with jsonschema --with pymupdf "
        "python corpus/pdf/validate.py",
        file=sys.stderr,
    )
    sys.exit(2)

ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "schema" / "expected-fields.schema.json"
LAYER1_DIR = ROOT / "layer1" / "scenarios"
LAYER2_DIR = ROOT / "layer2"
REPO_ROOT = ROOT.parent.parent

# Make ``corpus.pdf.generator.matrix`` importable when invoked as a script via
# ``uv run --with jsonschema --with pymupdf python corpus/pdf/validate.py``.
# ``corpus`` resolves as a PEP 420 namespace package (no __init__.py needed).
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Per-document-type minimum counts (see technical-considerations sec. 2.2).
MIN_STATIONS = {"air_ticket", "rail_ticket", "bus_ticket"}
MIN_ACCOMMODATIONS = {"hotel_booking", "airbnb_booking"}

# Document types whose stations must each carry a departure or arrival datetime
# (see technical-considerations sec. 2.2).
TRANSIT_TICKETS = {"air_ticket", "rail_ticket", "bus_ticket"}

# Cap on the number of drift snippets we print before falling back to
# scenario_id-only output, so a regen-disaster doesn't drown the terminal.
DRIFT_SNIPPET_LIMIT = 3
DRIFT_SNIPPET_LINES = 15

# Per-file check ordering. The keys here drive the report layout, so keep
# the iteration order stable (schema first, sanity last).
CHECK_ORDER: tuple[str, ...] = (
    "schema",
    "integrity",
    "min-count",
    "datetime",
    "drift",
    "sanity",
)


def _load_validator(path: Path) -> Draft202012Validator:
    schema = json.loads(path.read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _format_path(absolute_path: list[Any]) -> str:
    return "/" + "/".join(str(part) for part in absolute_path) if absolute_path else "<root>"


def _schema_errors(validator: Draft202012Validator, payload: Any) -> list[str]:
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path))
    return [f"{err.message} at {_format_path(list(err.absolute_path))}" for err in errors]


def _integrity_errors(payload: dict[str, Any]) -> list[str]:
    """City-integrity rule: every place's city must appear in cities[]."""
    cities = payload.get("cities")
    if not isinstance(cities, list):
        # Schema validation will have flagged this; nothing useful to check.
        return []
    city_set = set(cities)
    failures: list[str] = []
    for field in ("stations", "accommodations", "venues"):
        entries = payload.get(field)
        if not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            city = entry.get("city")
            if city is None:
                continue
            if city not in city_set:
                failures.append(
                    f"{field}[{index}].city {city!r} not in cities[] ({cities!r})"
                )
    return failures


def _datetime_errors(payload: dict[str, Any]) -> list[str]:
    """Transit-ticket stations must each carry departure or arrival datetime."""
    document_type = payload.get("document_type")
    if document_type not in TRANSIT_TICKETS:
        return []
    stations = payload.get("stations")
    if not isinstance(stations, list):
        return []
    failures: list[str] = []
    for index, entry in enumerate(stations):
        if not isinstance(entry, dict):
            continue
        if not (entry.get("departure_datetime") or entry.get("arrival_datetime")):
            failures.append(
                f"stations[{index}] for {document_type} has neither "
                "departure_datetime nor arrival_datetime"
            )
    return failures


def _min_count_errors(payload: dict[str, Any]) -> list[str]:
    """Per-document_type minimum counts."""
    document_type = payload.get("document_type")
    if not isinstance(document_type, str):
        return []
    failures: list[str] = []
    if document_type in MIN_STATIONS:
        stations = payload.get("stations")
        count = len(stations) if isinstance(stations, list) else 0
        if count < 2:
            failures.append(
                f"{document_type} requires stations[] >= 2, got {count}"
            )
    elif document_type in MIN_ACCOMMODATIONS:
        accommodations = payload.get("accommodations")
        count = len(accommodations) if isinstance(accommodations, list) else 0
        if count < 1:
            failures.append(
                f"{document_type} requires accommodations[] >= 1, got {count}"
            )
    return failures


def _discover_files() -> list[Path]:
    """Walk Layer 1 + Layer 2 and return every expected-fields JSON path."""
    files: list[Path] = []
    if LAYER1_DIR.exists():
        files.extend(sorted(LAYER1_DIR.glob("*/expected-fields.json")))
    if LAYER2_DIR.exists():
        files.extend(sorted(LAYER2_DIR.glob("**/*.expected-fields.json")))
    return files


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _validate_file(validator: Draft202012Validator, path: Path) -> dict[str, list[str]]:
    """Run the four structural per-file checks. Drift/sanity are added later."""
    result: dict[str, list[str]] = {check: [] for check in CHECK_ORDER}
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        result["schema"].append(f"invalid JSON: {exc}")
        return result

    result["schema"] = _schema_errors(validator, payload)
    # Only run the data-shape rules if the payload is actually a dict.
    if isinstance(payload, dict):
        result["integrity"] = _integrity_errors(payload)
        result["min-count"] = _min_count_errors(payload)
        result["datetime"] = _datetime_errors(payload)
    return result


# ---------------------------------------------------------------------------
# Drift check (Layer 1).
# ---------------------------------------------------------------------------


def _serialize_expected(spec: Any) -> str:
    """Mirror ``corpus/pdf/generator/__main__.py``: indent=2, sort_keys, \\n."""
    return json.dumps(spec.expected_fields(), indent=2, sort_keys=True) + "\n"


def _drift_snippet(expected: str, actual: str, label: str) -> str:
    """Short unified diff for one drifted scenario, capped in length."""
    diff_lines = list(
        difflib.unified_diff(
            actual.splitlines(keepends=True),
            expected.splitlines(keepends=True),
            fromfile=f"{label} (committed)",
            tofile=f"{label} (expected)",
            n=2,
        )
    )
    if len(diff_lines) > DRIFT_SNIPPET_LINES:
        diff_lines = diff_lines[:DRIFT_SNIPPET_LINES]
        diff_lines.append("... (truncated)\n")
    return "".join(diff_lines)


def _drift_check() -> tuple[list[str], list[str]]:
    """Layer-1 JSON drift. Returns ``(failures, warnings)``.

    Three categories of failure: per-scenario byte drift, orphan directories,
    missing-but-enumerated directories.
    """
    failures: list[str] = []
    warnings: list[str] = []

    try:
        from corpus.pdf.generator.matrix import enumerate_scenarios
    except ImportError as exc:
        warnings.append(
            f"drift: matrix module not importable — drift check skipped ({exc})"
        )
        return failures, warnings

    expected_specs = list(enumerate_scenarios())
    expected_ids: set[str] = {spec.scenario_id for spec in expected_specs}

    # Per-scenario drift comparison.
    drifted_scenarios: list[tuple[str, str, str]] = []
    for spec in sorted(expected_specs, key=lambda s: s.scenario_id):
        scenario_dir = LAYER1_DIR / spec.scenario_id
        json_path = scenario_dir / "expected-fields.json"
        expected_text = _serialize_expected(spec)
        if not json_path.exists():
            failures.append(
                f"drift: scenarios/{spec.scenario_id} missing expected-fields.json "
                "(matrix enumerated but not on disk)"
            )
            continue
        actual_text = json_path.read_text()
        if actual_text != expected_text:
            drifted_scenarios.append((spec.scenario_id, expected_text, actual_text))

    for index, (scenario_id, expected_text, actual_text) in enumerate(drifted_scenarios):
        label = f"scenarios/{scenario_id}"
        if index < DRIFT_SNIPPET_LIMIT:
            snippet = _drift_snippet(expected_text, actual_text, label)
            failures.append(
                f"drift: {label} expected != committed\n{snippet.rstrip()}"
            )
        else:
            failures.append(f"drift: {scenario_id} (committed != expected)")

    # Orphan-directory detection: anything on disk the matrix didn't enumerate.
    if LAYER1_DIR.exists():
        for entry in sorted(LAYER1_DIR.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name not in expected_ids:
                failures.append(
                    f"drift: scenarios/{entry.name} on disk but not enumerated "
                    "by matrix (orphan or stale scenario)"
                )

    return failures, warnings


# ---------------------------------------------------------------------------
# PDF/JSON token sanity.
# ---------------------------------------------------------------------------


def _expected_tokens(payload: dict[str, Any]) -> list[str]:
    """Cities + distinct ``YYYY-MM-DD`` date prefixes (deterministic order)."""
    tokens: list[str] = []
    seen: set[str] = set()

    def _add(token: Any) -> None:
        if not isinstance(token, str):
            return
        if token in seen:
            return
        seen.add(token)
        tokens.append(token)

    for city in payload.get("cities", []) or []:
        _add(city)

    dates: set[str] = set()
    for field in ("stations", "accommodations", "venues"):
        for entry in payload.get(field, []) or []:
            if not isinstance(entry, dict):
                continue
            for key, value in entry.items():
                if not key.endswith("_datetime"):
                    continue
                if not isinstance(value, str) or len(value) < 10:
                    continue
                dates.add(value[:10])

    for date in sorted(dates):
        _add(date)

    return tokens


def _extract_pdf_text(pdf_path: Path, pymupdf_module: Any) -> str:
    """Open the PDF with PyMuPDF and concatenate all-page text."""
    doc = pymupdf_module.open(str(pdf_path))
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def _resolve_pdf_path(json_path: Path) -> Path:
    """Layer 1 -> ``document.pdf``; Layer 2 -> swap ``.expected-fields.json`` for ``.pdf``."""
    if json_path.name == "expected-fields.json":
        return json_path.parent / "document.pdf"
    name = json_path.name
    if name.endswith(".expected-fields.json"):
        return json_path.with_name(name[: -len(".expected-fields.json")] + ".pdf")
    return json_path.with_suffix(".pdf")


def _sanity_check_file(
    json_path: Path, payload: dict[str, Any], pymupdf_module: Any
) -> list[str]:
    """Token-presence for ``text``; empty-text-layer for ``rasterized``."""
    pdf_path = _resolve_pdf_path(json_path)
    if not pdf_path.exists():
        return [f"PDF not found at {_rel(pdf_path)}"]

    try:
        text = _extract_pdf_text(pdf_path, pymupdf_module)
    except Exception as exc:  # noqa: BLE001 — pymupdf raises a wide variety
        return [f"failed to read {_rel(pdf_path)}: {exc}"]

    pdf_kind = payload.get("pdf_kind")
    if pdf_kind == "rasterized":
        stripped = text.strip()
        if stripped:
            # Collapse whitespace and truncate so the report stays one-line;
            # the snippet makes a real-world flip ("this PDF actually has text")
            # immediately obvious in CI without dumping the whole page.
            preview = " ".join(stripped.split())
            if len(preview) > 50:
                preview = preview[:47] + "..."
            return [
                f"rasterized PDF {_rel(pdf_path)} has non-empty text "
                f"layer (got {preview!r})"
            ]
        return []

    if pdf_kind != "text":
        # Unknown pdf_kind values are a schema concern; nothing to sanity-check.
        return []

    failures: list[str] = []
    if not text.strip():
        failures.append(
            f"{_rel(pdf_path)} pdf_kind=text but text layer is empty"
        )
        return failures
    for token in _expected_tokens(payload):
        if token not in text:
            failures.append(f"{token!r} not found in {_rel(pdf_path)}")
    return failures


def _load_pymupdf() -> tuple[Any | None, str | None]:
    """Try to import PyMuPDF. Returns ``(module, warning_or_none)``."""
    try:
        import pymupdf  # type: ignore[import-not-found]
    except ImportError as exc:
        return None, (
            f"sanity: pymupdf not importable — PDF/JSON sanity check skipped "
            f"({exc})"
        )
    return pymupdf, None


# ---------------------------------------------------------------------------
# Orchestrator.
# ---------------------------------------------------------------------------


def _print_indented(prefix: str, message: str, indent: str) -> None:
    """Print ``message``'s first line behind ``prefix`` and subsequent lines indented."""
    lines = message.splitlines() or [""]
    print(f"{prefix}{lines[0]}")
    for extra in lines[1:]:
        print(f"{indent}{extra}")


def main() -> int:
    if not SCHEMA_PATH.exists():
        print(f"ERROR: schema does not exist: {SCHEMA_PATH}", file=sys.stderr)
        return 2

    validator = _load_validator(SCHEMA_PATH)
    files = _discover_files()

    file_results: list[tuple[Path, dict[str, list[str]], dict[str, Any] | None]] = []
    for path in files:
        results = _validate_file(validator, path)
        try:
            payload: dict[str, Any] | None = json.loads(path.read_text())
            if not isinstance(payload, dict):
                payload = None
        except json.JSONDecodeError:
            payload = None
        file_results.append((path, results, payload))

    drift_failures, drift_warnings = _drift_check()
    for warning in drift_warnings:
        print(f"WARNING: {warning}")

    pymupdf_module, sanity_warning = _load_pymupdf()
    if sanity_warning is not None:
        print(f"WARNING: {sanity_warning}")
    elif pymupdf_module is not None:
        for json_path, results, payload in file_results:
            if payload is None:
                continue
            results["sanity"].extend(
                _sanity_check_file(json_path, payload, pymupdf_module)
            )

    passed = 0
    failed = 0
    for path, results, _payload in file_results:
        if not any(results.values()):
            passed += 1
            continue
        failed += 1
        print(f"{_rel(path)}: FAIL")
        for check in CHECK_ORDER:
            for message in results[check]:
                _print_indented(f"  {check}: ", message, "    ")

    if drift_failures:
        print(f"Drift check failed ({len(drift_failures)} differences):")
        for failure in drift_failures:
            _print_indented("  - ", failure, "    ")

    total = passed + failed
    print(f"Validated {total} files: {passed} passed, {failed} failed")
    return 1 if failed or drift_failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — last-resort sentinel
        print(f"ERROR: unexpected validator failure: {exc}", file=sys.stderr)
        sys.exit(2)
