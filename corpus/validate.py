"""Validate the corpus.

Steps:
1. Schema-validate every ``corpus/scenarios/*/fragments/*.json`` against
   ``corpus/schema/extracted-fragment.schema.json``.
2. Schema-validate every ``corpus/scenarios/*/expected-route.json`` against
   ``corpus/schema/expected-route.schema.json``.
3. Drift check: regenerate the corpus into a tempdir and diff it against
   ``corpus/scenarios/``. Any difference (extra/missing/changed file) fails.

Invocation::

    uv run --with jsonschema python corpus/validate.py

Exits non-zero on any failure.
"""

from __future__ import annotations

import filecmp
import json
import sys
import tempfile
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover - guidance for direct invocation
    print(
        "ERROR: jsonschema is not installed. Run via: "
        "uv run --with jsonschema python corpus/validate.py",
        file=sys.stderr,
    )
    sys.exit(2)

ROOT = Path(__file__).resolve().parent
SCHEMA_DIR = ROOT / "schema"
SCENARIOS_DIR = ROOT / "scenarios"

FRAGMENT_SCHEMA_PATH = SCHEMA_DIR / "extracted-fragment.schema.json"
ROUTE_SCHEMA_PATH = SCHEMA_DIR / "expected-route.schema.json"


def _load_validator(path: Path) -> Draft202012Validator:
    schema = json.loads(path.read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _validate_json(validator: Draft202012Validator, path: Path) -> list[str]:
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return [f"{path}: invalid JSON: {exc}"]
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path))
    return [f"{path}: {err.message} (at /{'/'.join(map(str, err.absolute_path))})" for err in errors]


def schema_validate(scenarios_dir: Path) -> tuple[int, list[str]]:
    fragment_validator = _load_validator(FRAGMENT_SCHEMA_PATH)
    route_validator = _load_validator(ROUTE_SCHEMA_PATH)
    failures: list[str] = []
    scenario_count = 0

    for scenario_folder in sorted(scenarios_dir.iterdir()):
        if not scenario_folder.is_dir():
            continue
        scenario_count += 1
        for fragment_path in sorted((scenario_folder / "fragments").glob("*.json")):
            failures.extend(_validate_json(fragment_validator, fragment_path))
        expected_route = scenario_folder / "expected-route.json"
        if not expected_route.exists():
            failures.append(f"{expected_route}: missing")
            continue
        failures.extend(_validate_json(route_validator, expected_route))
    return scenario_count, failures


def _diff_dirs(left: Path, right: Path) -> list[str]:
    """Recursively diff two directory trees. Returns human-readable failures."""
    diffs: list[str] = []
    cmp = filecmp.dircmp(left, right)
    stack = [cmp]
    while stack:
        node = stack.pop()
        rel = Path(node.left).relative_to(left)
        for name in node.left_only:
            diffs.append(f"missing in regenerated: {rel / name}")
        for name in node.right_only:
            diffs.append(f"extra in regenerated: {rel / name}")
        for name in node.diff_files:
            diffs.append(f"content drift: {rel / name}")
        for name in node.funny_files:
            diffs.append(f"unreadable: {rel / name}")
        for sub in node.subdirs.values():
            stack.append(sub)
    return diffs


def drift_check(scenarios_dir: Path) -> list[str]:
    # Import lazily so schema-only failures still report cleanly.
    from corpus.generator.__main__ import run as regenerate

    with tempfile.TemporaryDirectory(prefix="corpus-regen-") as tmp:
        tmp_path = Path(tmp) / "scenarios"
        rc = regenerate(tmp_path)
        if rc != 0:
            return [f"generator exited with code {rc}"]
        return _diff_dirs(scenarios_dir, tmp_path)


def main() -> int:
    if not SCENARIOS_DIR.exists():
        print(f"ERROR: scenarios directory does not exist: {SCENARIOS_DIR}", file=sys.stderr)
        return 1

    # Make ``corpus.generator`` importable when invoked as a script.
    sys.path.insert(0, str(ROOT.parent))

    scenario_count, schema_failures = schema_validate(SCENARIOS_DIR)
    if schema_failures:
        print(f"Schema validation failed ({len(schema_failures)} errors):", file=sys.stderr)
        for failure in schema_failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    drift_failures = drift_check(SCENARIOS_DIR)
    if drift_failures:
        print(f"Drift check failed ({len(drift_failures)} differences):", file=sys.stderr)
        for failure in drift_failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print(f"OK: {scenario_count} scenarios validated, no drift.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
