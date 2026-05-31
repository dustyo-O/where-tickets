"""PDF-corpus runner: discover scenarios, call the extractor, report PASS/FAIL.

For every Layer 1 scenario (``corpus/pdf/layer1/scenarios/<slug>/document.pdf``
paired with sibling ``expected-fields.json``) and every Layer 2 trip PDF
(``corpus/pdf/layer2/<trip>/<pdfstem>.pdf`` paired with sibling
``<pdfstem>.expected-fields.json``), the runner:

1. Loads the expected-fields JSON.
2. Calls the extractor: ``extract_pdf(pdf_path) -> ExtractedFields``.
3. Compares the extracted fields against the expected ones, field by field.
4. Aggregates PASS / FAIL counts and prints a per-layer summary, plus a
   ``FAILED:`` block listing the specific field mismatches.

The extractor module is imported lazily; the default import path is
``where_tickets.extraction.extract_pdf`` and can be overridden via
``--extractor-import-path`` (used by tests with a stub). If the module cannot
be imported the runner prints a banner, skips all per-scenario work, and
exits ``0`` — a missing extractor is not a corpus failure.

Invocation (matches ``corpus/pdf/validate.py`` style)::

    python corpus/pdf/runner.py [--layer 1|2|both] [--filter SUBSTR] \\
        [--extractor-import-path DOTTED.PATH] [--json-report PATH]

Exit codes:
- ``0`` — all comparisons passed (including the extractor-not-wired case and
  the zero-scenarios case).
- ``1`` — at least one scenario failed comparison.
- ``2`` — unexpected error at the top level.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, NotRequired, Protocol, TypedDict


# ---------------------------------------------------------------------------
# Extractor interface — mirrors expected-fields.schema.json (sans corpus-only
# metadata: scenario_id / noise_seed) plus an optional extraction_path tag.
# ---------------------------------------------------------------------------


class StationEntry(TypedDict):
    city: str
    kind: Literal["airport", "rail_station", "bus_terminal"]
    identifier: str
    departure_datetime: NotRequired[str]
    arrival_datetime: NotRequired[str]


class AccommodationEntry(TypedDict):
    city: str
    kind: Literal["hotel", "airbnb"]
    identifier: str
    check_in_datetime: str
    check_out_datetime: str


class VenueEntry(TypedDict):
    city: str
    kind: Literal["sightseeing", "parking", "other"]
    identifier: str
    valid_from_datetime: NotRequired[str]
    valid_to_datetime: NotRequired[str]


class PriceEntry(TypedDict):
    amount: float
    currency: str


class ExtractedFields(TypedDict):
    document_type: Literal[
        "air_ticket",
        "rail_ticket",
        "bus_ticket",
        "hotel_booking",
        "airbnb_booking",
        "supplementary",
    ]
    cities: list[str]
    stations: list[StationEntry]
    accommodations: list[AccommodationEntry]
    venues: list[VenueEntry]
    travelers: list[str]
    prices: list[PriceEntry]
    qr_codes: list[str]
    pdf_kind: Literal["text", "rasterized"]
    extraction_path: NotRequired[Literal["text", "vision"]]


class Extractor(Protocol):
    """Runtime contract for the document-extraction callable.

    Implemented by ``where_tickets.extraction.extract_pdf`` once the AI
    Document Understanding spec lands.
    """

    def __call__(self, pdf_path: Path) -> ExtractedFields: ...


# ---------------------------------------------------------------------------
# Paths — locate the corpus relative to the source file, not cwd.
# ---------------------------------------------------------------------------


PDF_ROOT = Path(__file__).resolve().parent
DEFAULT_REPO_ROOT = PDF_ROOT.parent.parent


def _repo_root() -> Path:
    override = os.environ.get("WT_REPO_ROOT")
    return Path(override).resolve() if override else DEFAULT_REPO_ROOT


def _layer1_dir() -> Path:
    return _repo_root() / "corpus" / "pdf" / "layer1" / "scenarios"


def _layer2_dir() -> Path:
    return _repo_root() / "corpus" / "pdf" / "layer2"


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(_repo_root()))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# Discovery.
# ---------------------------------------------------------------------------


Layer = Literal["1", "2"]


class DiscoveredScenario(TypedDict):
    layer: Layer
    pdf_path: Path
    expected_path: Path
    label: str  # short label used in the FAILED: block, e.g. "L1 001-foo/document.pdf"


def _discover_layer1() -> list[DiscoveredScenario]:
    root = _layer1_dir()
    if not root.exists():
        return []
    scenarios: list[DiscoveredScenario] = []
    for scenario_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        pdf_path = scenario_dir / "document.pdf"
        expected_path = scenario_dir / "expected-fields.json"
        if pdf_path.exists() and expected_path.exists():
            scenarios.append(
                DiscoveredScenario(
                    layer="1",
                    pdf_path=pdf_path,
                    expected_path=expected_path,
                    label=f"L1 {scenario_dir.name}/{pdf_path.name}",
                )
            )
    return scenarios


def _discover_layer2() -> list[DiscoveredScenario]:
    root = _layer2_dir()
    if not root.exists():
        return []
    scenarios: list[DiscoveredScenario] = []
    for pdf_path in sorted(root.glob("*/*.pdf")):
        expected_path = pdf_path.with_suffix(".expected-fields.json")
        if expected_path.exists():
            scenarios.append(
                DiscoveredScenario(
                    layer="2",
                    pdf_path=pdf_path,
                    expected_path=expected_path,
                    label=f"L2 {pdf_path.parent.name}/{pdf_path.name}",
                )
            )
    return scenarios


def _discover(layer: Literal["1", "2", "both"], pattern: str) -> list[DiscoveredScenario]:
    scenarios: list[DiscoveredScenario] = []
    if layer in ("1", "both"):
        scenarios.extend(_discover_layer1())
    if layer in ("2", "both"):
        scenarios.extend(_discover_layer2())
    if pattern:
        scenarios = [s for s in scenarios if pattern in str(s["pdf_path"])]
    return scenarios


# ---------------------------------------------------------------------------
# Comparison.
# ---------------------------------------------------------------------------


# Fields on each structured-place entry that we compare (ordered list compare).
_STATION_FIELDS = ("city", "kind", "identifier", "departure_datetime", "arrival_datetime")
_ACCOMMODATION_FIELDS = ("city", "kind", "identifier", "check_in_datetime", "check_out_datetime")
_VENUE_FIELDS = ("city", "kind", "identifier", "valid_from_datetime", "valid_to_datetime")


def _fmt(value: Any) -> str:
    """Readable representation for diff lines."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, default=str)
    return repr(value)


def _compare_scalar(name: str, expected: Any, actual: Any) -> list[str]:
    if expected == actual:
        return []
    return [f"{name}: expected {_fmt(expected)}   actual {_fmt(actual)}"]


def _compare_set(name: str, expected: list[Any], actual: list[Any]) -> list[str]:
    if sorted(expected) == sorted(actual):
        return []
    return [
        f"{name}: expected {_fmt(sorted(expected))}   actual {_fmt(sorted(actual))}"
    ]


def _compare_place_list(
    name: str,
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
    fields: tuple[str, ...],
) -> list[str]:
    """Ordered list comparison; report the first mismatched field per index."""
    failures: list[str] = []
    if len(expected) != len(actual):
        failures.append(
            f"{name}: length mismatch — expected {len(expected)} entries, "
            f"actual {len(actual)}"
        )
        return failures
    for index, (exp_entry, act_entry) in enumerate(zip(expected, actual, strict=True)):
        for field in fields:
            exp_val = exp_entry.get(field)
            act_val = act_entry.get(field)
            if exp_val != act_val:
                failures.append(
                    f"{name}[{index}].{field}: "
                    f"expected {_fmt(exp_val)}   actual {_fmt(act_val)}"
                )
    return failures


def _price_key(price: dict[str, Any]) -> tuple[float, str]:
    return (float(price.get("amount", 0)), str(price.get("currency", "")))


def _compare_prices(
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
) -> list[str]:
    exp_sorted = sorted(expected, key=_price_key)
    act_sorted = sorted(actual, key=_price_key)
    if len(exp_sorted) != len(act_sorted):
        return [
            f"prices: length mismatch — expected {len(exp_sorted)}, actual {len(act_sorted)}"
        ]
    canonical_match = all(
        _price_key(e) == _price_key(a) for e, a in zip(exp_sorted, act_sorted, strict=True)
    )
    literal_match = exp_sorted == act_sorted
    if literal_match:
        return []
    if canonical_match:
        return [
            f"prices: numeric-format mismatch (canonical values match) — "
            f"expected {_fmt(exp_sorted)}   actual {_fmt(act_sorted)}"
        ]
    return [f"prices: expected {_fmt(exp_sorted)}   actual {_fmt(act_sorted)}"]


def _strip_corpus_metadata(expected: dict[str, Any]) -> dict[str, Any]:
    """Drop scenario_id / noise_seed — they're not part of the extractor output."""
    return {k: v for k, v in expected.items() if k not in {"scenario_id", "noise_seed"}}


def compare(
    expected: dict[str, Any],
    actual: dict[str, Any],
) -> list[str]:
    """Return a list of human-readable mismatch lines (empty == PASS)."""
    failures: list[str] = []
    expected = _strip_corpus_metadata(expected)

    failures.extend(
        _compare_scalar("document_type", expected.get("document_type"), actual.get("document_type"))
    )
    failures.extend(
        _compare_scalar("pdf_kind", expected.get("pdf_kind"), actual.get("pdf_kind"))
    )
    failures.extend(
        _compare_set("cities", expected.get("cities", []), actual.get("cities", []))
    )
    failures.extend(
        _compare_set("travelers", expected.get("travelers", []), actual.get("travelers", []))
    )
    failures.extend(
        _compare_set("qr_codes", expected.get("qr_codes", []), actual.get("qr_codes", []))
    )
    failures.extend(
        _compare_place_list(
            "stations",
            expected.get("stations", []),
            actual.get("stations", []),
            _STATION_FIELDS,
        )
    )
    failures.extend(
        _compare_place_list(
            "accommodations",
            expected.get("accommodations", []),
            actual.get("accommodations", []),
            _ACCOMMODATION_FIELDS,
        )
    )
    failures.extend(
        _compare_place_list(
            "venues",
            expected.get("venues", []),
            actual.get("venues", []),
            _VENUE_FIELDS,
        )
    )
    failures.extend(_compare_prices(expected.get("prices", []), actual.get("prices", [])))

    return failures


# ---------------------------------------------------------------------------
# Per-scenario result + reporting.
# ---------------------------------------------------------------------------


class ScenarioResult(TypedDict):
    layer: Layer
    label: str
    passed: bool
    failures: list[str]
    extraction_path: str | None  # collected but not displayed at this slice


def _accuracy(passed: int, total: int) -> str:
    if total == 0:
        return " n/a %"
    return f"{(passed / total) * 100:.1f}%"


def _print_summary(results: list[ScenarioResult], extractor_wired: bool) -> None:
    layer1 = [r for r in results if r["layer"] == "1"]
    layer2 = [r for r in results if r["layer"] == "2"]

    if not extractor_wired:
        l1_total = len(layer1)
        l2_total = len(layer2)
        total = l1_total + l2_total
        print(f"Layer 1 (synthetic): 0/{l1_total} skipped")
        print(f"Layer 2 (real):      0/{l2_total} skipped")
        print(f"TOTAL:               0/{total} skipped — extractor not wired")
        return

    def _line(label: str, group: list[ScenarioResult]) -> str:
        total = len(group)
        passed = sum(1 for r in group if r["passed"])
        return f"{label} {passed}/{total} PASS  ({_accuracy(passed, total)})"

    print(_line("Layer 1 (synthetic):", layer1))
    print(_line("Layer 2 (real):     ", layer2))
    print(_line("TOTAL:              ", results))

    failed = [r for r in results if not r["passed"]]
    if failed:
        print()
        print("FAILED:")
        for result in failed:
            print(f"  {result['label']}")
            for failure in result["failures"]:
                print(f"    {failure}")


def _write_json_report(
    path: Path,
    results: list[ScenarioResult],
    extractor_wired: bool,
) -> None:
    summary = {
        "extractor_wired": extractor_wired,
        "layer1_total": sum(1 for r in results if r["layer"] == "1"),
        "layer1_passed": sum(1 for r in results if r["layer"] == "1" and r["passed"]),
        "layer2_total": sum(1 for r in results if r["layer"] == "2"),
        "layer2_passed": sum(1 for r in results if r["layer"] == "2" and r["passed"]),
        "total": len(results),
        "passed": sum(1 for r in results if r["passed"]),
    }
    failures = [
        {"label": r["label"], "layer": r["layer"], "mismatches": r["failures"]}
        for r in results
        if not r["passed"]
    ]
    path.write_text(
        json.dumps(
            {"version": 1, "summary": summary, "failures": failures},
            indent=2,
            sort_keys=True,
        )
    )


# ---------------------------------------------------------------------------
# Extractor loading.
# ---------------------------------------------------------------------------


def _load_extractor(dotted_path: str) -> Callable[[Path], ExtractedFields] | None:
    """Import the extractor callable by dotted path; return None if unavailable.

    Expects ``pkg.module.attr`` form — splits on the final dot.
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
    return candidate


# ---------------------------------------------------------------------------
# CLI / main.
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the PDF corpus against an extractor and report PASS/FAIL.",
    )
    parser.add_argument(
        "--layer",
        choices=("1", "2", "both"),
        default="both",
        help="Which corpus layer to run (default: both).",
    )
    parser.add_argument(
        "--filter",
        default="",
        help="Substring filter applied to each PDF's path (default: no filter).",
    )
    parser.add_argument(
        "--extractor-import-path",
        default="where_tickets.extraction.extract_pdf",
        help="Dotted import path of the extractor callable "
        "(default: where_tickets.extraction.extract_pdf).",
    )
    parser.add_argument(
        "--json-report",
        default=None,
        help="If set, also write a machine-readable JSON report to this path.",
    )
    return parser.parse_args(argv)


def _run_scenarios(
    scenarios: list[DiscoveredScenario],
    extractor: Callable[[Path], ExtractedFields],
) -> list[ScenarioResult]:
    results: list[ScenarioResult] = []
    for scenario in scenarios:
        expected_payload = json.loads(scenario["expected_path"].read_text())
        try:
            actual: dict[str, Any] = dict(extractor(scenario["pdf_path"]))
        except Exception as exc:  # noqa: BLE001 — surface as a per-file failure
            results.append(
                ScenarioResult(
                    layer=scenario["layer"],
                    label=scenario["label"],
                    passed=False,
                    failures=[f"extractor raised {type(exc).__name__}: {exc}"],
                    extraction_path=None,
                )
            )
            continue
        failures = compare(expected_payload, actual)
        results.append(
            ScenarioResult(
                layer=scenario["layer"],
                label=scenario["label"],
                passed=not failures,
                failures=failures,
                extraction_path=actual.get("extraction_path"),
            )
        )
    return results


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        scenarios = _discover(args.layer, args.filter)

        extractor = _load_extractor(args.extractor_import_path)

        if extractor is None:
            print("extractor not wired: install per AI Document Understanding spec")
            print()
            skipped_results = [
                ScenarioResult(
                    layer=s["layer"],
                    label=s["label"],
                    passed=False,
                    failures=[],
                    extraction_path=None,
                )
                for s in scenarios
            ]
            _print_summary(skipped_results, extractor_wired=False)
            if args.json_report:
                _write_json_report(
                    Path(args.json_report),
                    skipped_results,
                    extractor_wired=False,
                )
            return 0

        results = _run_scenarios(scenarios, extractor)
        _print_summary(results, extractor_wired=True)
        if args.json_report:
            _write_json_report(Path(args.json_report), results, extractor_wired=True)

        return 1 if any(not r["passed"] for r in results) else 0
    except Exception:  # noqa: BLE001 — top-level safety net
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
