"""Per-PDF structural validation for the mock-document corpus.

For each ``expected-fields.json`` found under Layer 1 and Layer 2, run four
checks:

1. JSON Schema validation against
   ``corpus/pdf/schema/expected-fields.schema.json`` (Draft 2020-12).
2. City-integrity: every ``stations[].city`` / ``accommodations[].city`` /
   ``venues[].city`` must appear in the top-level ``cities[]``.
3. Per-``document_type`` minimum counts:
   - ``air_ticket`` / ``rail_ticket`` / ``bus_ticket`` -> ``len(stations) >= 2``
   - ``hotel_booking`` / ``airbnb_booking`` -> ``len(accommodations) >= 1``
   - ``supplementary`` -> no minimum.
4. Datetime-presence on transit-ticket stations: for
   ``air_ticket`` / ``rail_ticket`` / ``bus_ticket`` documents, every entry in
   ``stations[]`` must carry at least one of ``departure_datetime`` /
   ``arrival_datetime``.

Drift checks, PDF/JSON token sanity, the Layer 2 leak guard, and cross-schema
validation are explicitly out of scope here (see technical-considerations
sec. 2.7 — those land in later slices).

Invocation::

    uv run --python 3.12 --with jsonschema python corpus/pdf/validate.py

Exits ``0`` if every file passes, ``1`` if any file fails.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

try:
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover - guidance for direct invocation
    print(
        "ERROR: jsonschema is not installed. Run via: "
        "uv run --python 3.12 --with jsonschema python corpus/pdf/validate.py",
        file=sys.stderr,
    )
    sys.exit(2)

ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "schema" / "expected-fields.schema.json"
LAYER1_DIR = ROOT / "layer1" / "scenarios"
LAYER2_DIR = ROOT / "layer2"
REPO_ROOT = ROOT.parent.parent

# Per-document-type minimum counts (see technical-considerations sec. 2.2).
MIN_STATIONS = {"air_ticket", "rail_ticket", "bus_ticket"}
MIN_ACCOMMODATIONS = {"hotel_booking", "airbnb_booking"}

# Document types whose stations must each carry a departure or arrival datetime
# (see technical-considerations sec. 2.2).
TRANSIT_TICKETS = {"air_ticket", "rail_ticket", "bus_ticket"}


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
    """Run all four checks. Returns a dict of check_name -> failures."""
    result: dict[str, list[str]] = {
        "schema": [],
        "integrity": [],
        "min-count": [],
        "datetime": [],
    }
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


def main() -> int:
    if not SCHEMA_PATH.exists():
        print(f"ERROR: schema does not exist: {SCHEMA_PATH}", file=sys.stderr)
        return 2

    validator = _load_validator(SCHEMA_PATH)
    files = _discover_files()

    passed = 0
    failed = 0
    for path in files:
        results = _validate_file(validator, path)
        if not any(results.values()):
            passed += 1
            continue
        failed += 1
        print(f"{_rel(path)}: FAIL")
        for check in ("schema", "integrity", "min-count", "datetime"):
            for message in results[check]:
                print(f"  {check}: {message}")

    total = passed + failed
    print(f"Validated {total} files: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
