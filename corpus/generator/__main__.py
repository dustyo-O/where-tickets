"""CLI entry: ``python -m corpus.generator [--output-dir PATH]``.

Writes the full scenario corpus into ``--output-dir`` (default
``corpus/scenarios``), replacing any prior contents of that directory.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from .matrix import build_matrix
from .scenario import GeneratedScenario, generate_scenario

DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "scenarios"


def _dump_json(payload: object) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _doc_type_suffix(doc_type: str) -> str:
    # Stable filename suffix per document type.
    return doc_type


def write_scenario(root: Path, scenario: GeneratedScenario) -> None:
    folder = root / scenario.spec.slug
    fragments_dir = folder / "fragments"
    fragments_dir.mkdir(parents=True, exist_ok=True)

    for idx, fragment in enumerate(scenario.fragments_in_emit_order, start=1):
        filename = f"{idx:02d}-{_doc_type_suffix(fragment['documentType'])}.json"
        (fragments_dir / filename).write_text(_dump_json(fragment))

    (folder / "expected-route.json").write_text(_dump_json(scenario.expected_route))
    (folder / "README.md").write_text(scenario.summary + "\n")


def run(output_dir: Path) -> int:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    specs = build_matrix()
    for spec in specs:
        scenario = generate_scenario(spec)
        write_scenario(output_dir, scenario)
    print(f"Generated {len(specs)} scenarios into {output_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Where to write scenarios (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args(argv)
    return run(args.output_dir)


if __name__ == "__main__":
    sys.exit(main())
