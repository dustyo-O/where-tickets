"""CLI entry: ``python -m corpus.pdf.generator [--output-dir PATH] [--scenario-filter SUBSTR]``.

Enumerates the air-ticket scenario matrix, renders each one as a real
text-layer PDF via WeasyPrint, and emits the ground-truth
``expected-fields.json`` and one-line ``README.md`` alongside.

Output layout per scenario::

    <output-dir>/<scenario_id>/
        document.pdf
        expected-fields.json
        README.md

Pre-step: deletes the contents of ``<output-dir>`` (but not the directory
itself) so stale fixtures from previous slices go away. Default output dir is
the real ``corpus/pdf/layer1/scenarios/`` tree.

The JSON layer is fully deterministic — two runs produce byte-identical
``expected-fields.json`` files. The PDF layer is *intentionally* not
byte-stable: noise.py randomizes layout choices per ``noise_seed`` and
re-running is allowed to (in principle) produce a different PDF given the
same seed only if the python ``random`` algorithm changes, which is rare in
practice but allowed by the spec.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from corpus.pdf.generator.matrix import ScenarioSpec, enumerate_scenarios
from corpus.pdf.generator.noise import (
    MARKETING_BANNERS,
    NoiseChoices,
    pick_noise,
)
from corpus.pdf.generator.render import render_pdf

# Default output directory: the real Layer 1 scenarios tree. Tests override
# this with a tmpdir via the CLI flag.
DEFAULT_OUTPUT: Path = Path(__file__).resolve().parent.parent / "layer1" / "scenarios"

# Single document type for Slice 3.
AIR_TICKET_TEMPLATE: str = "air-ticket.html.j2"


def _split_datetime(value: str | None) -> tuple[str, str]:
    """Split an ISO local datetime into ``(YYYY-MM-DD, HH:MM)`` strings.

    Returns ``("", "")`` if the value is missing. The date half is what the
    validator's PDF/JSON token-presence check looks for, so it MUST land
    verbatim in the rendered template.
    """
    if not value:
        return "", ""
    date_part, _, time_part = value.partition("T")
    # The schema's `isoLocalDatetime` is `YYYY-MM-DDTHH:MM:SS`; keep just
    # hours and minutes for human-friendly print on the ticket.
    return date_part, time_part[:5] if time_part else ""


def _build_legs(stations: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Pair consecutive stations into journey legs for the template.

    The spec layout pairs station[i] (departing) with station[i+1] (arriving)
    for each leg the document represents:

    - ``one_leg`` (2 stations) -> one leg.
    - ``return`` (3 stations: A-out, B-turnaround, A-back) -> two legs:
        leg 1: A departs -> B arrives
        leg 2: B departs -> A arrives

    Both shapes satisfy "every consecutive pair where the first has
    ``departure_datetime`` and the second has ``arrival_datetime`` is one
    leg". Anything else would be a matrix bug.
    """
    legs: list[dict[str, str]] = []
    for i in range(len(stations) - 1):
        origin = stations[i]
        destination = stations[i + 1]
        if not origin.get("departure_datetime") or not destination.get(
            "arrival_datetime"
        ):
            # Defensive: matrix.py is expected to keep these aligned.
            continue
        dep_date, dep_time = _split_datetime(origin.get("departure_datetime"))
        arr_date, arr_time = _split_datetime(destination.get("arrival_datetime"))
        legs.append(
            {
                "origin_city": str(origin["city"]),
                "origin_iata": str(origin["identifier"]),
                "destination_city": str(destination["city"]),
                "destination_iata": str(destination["identifier"]),
                "departure_date": dep_date,
                "departure_time": dep_time,
                "arrival_date": arr_date,
                "arrival_time": arr_time,
            }
        )
    return legs


def _build_context(
    spec: ScenarioSpec, fields: dict[str, Any], noise: NoiseChoices
) -> dict[str, Any]:
    """Assemble the Jinja2 render context for one scenario."""
    legs = _build_legs(list(fields.get("stations", [])))
    if not legs:
        raise ValueError(
            f"scenario {spec.scenario_id} produced zero legs; "
            "stations[] is malformed for the air-ticket template"
        )
    # Banners are selected from a fixed catalog by index so the rendered text
    # stays inside the catalog. The count itself is the noise axis.
    banners = list(MARKETING_BANNERS[: noise.marketing_banner_count])
    return {
        "data": fields,
        "noise": noise,
        "legs": legs,
        "banners": banners,
        "tc_block": noise.tc_block,
        "footer_variant": noise.footer_variant,
        "qr_codes": list(fields.get("qr_codes", [])),
    }


def _summary_line(spec: ScenarioSpec, fields: dict[str, Any]) -> str:
    """One-line README content: shape + travelers + cities."""
    cities = " -> ".join(fields.get("cities", []))
    return (
        f"{spec.scenario_id}: shape={spec.shape}, travelers={spec.travelers}, "
        f"cities={cities}\n"
    )


def _clean_output_contents(output_dir: Path) -> None:
    """Delete the contents of ``output_dir`` without removing the directory.

    Keeps the dir itself so a watching process (or tracked path in git) is not
    confused. Layer 1 lives under a committed parent.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    for entry in output_dir.iterdir():
        if entry.is_dir():
            _rmtree(entry)
        else:
            entry.unlink()


def _rmtree(path: Path) -> None:
    """Recursive delete — kept tiny to avoid pulling in shutil at import."""
    for child in path.iterdir():
        if child.is_dir():
            _rmtree(child)
        else:
            child.unlink()
    path.rmdir()


def _write_scenario(
    output_dir: Path,
    spec: ScenarioSpec,
) -> None:
    """Render one scenario and write all three sibling files."""
    fields = spec.expected_fields()
    noise = pick_noise(spec.noise_seed)
    context = _build_context(spec, fields, noise)

    scenario_dir = output_dir / spec.scenario_id
    scenario_dir.mkdir(parents=True, exist_ok=True)

    render_pdf(
        AIR_TICKET_TEMPLATE,
        scenario_dir / "document.pdf",
        context=context,
    )

    # `sort_keys=True` makes the JSON byte-stable across runs.
    json_text = json.dumps(fields, indent=2, sort_keys=True) + "\n"
    (scenario_dir / "expected-fields.json").write_text(json_text)

    (scenario_dir / "README.md").write_text(_summary_line(spec, fields))


def run(output_dir: Path, scenario_filter: str | None = None) -> int:
    output_dir = output_dir.resolve()
    _clean_output_contents(output_dir)

    specs = sorted(enumerate_scenarios(), key=lambda s: s.scenario_id)
    if scenario_filter:
        specs = [s for s in specs if scenario_filter in s.scenario_id]

    for spec in specs:
        _write_scenario(output_dir, spec)

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
    parser.add_argument(
        "--scenario-filter",
        type=str,
        default=None,
        help="Render only scenarios whose ID contains this substring.",
    )
    args = parser.parse_args(argv)
    return run(args.output_dir, scenario_filter=args.scenario_filter)


if __name__ == "__main__":
    sys.exit(main())
