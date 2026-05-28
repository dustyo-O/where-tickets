"""Offline tests for the cross-model compare report (synthetic results.json).

No network, no ``anthropic``, no AWS. Feeds ``build_compare_md`` hand-built
payloads matching the producer's results.json schema and asserts the headline
numbers, per-axis columns, failure buckets, >=99% bar verdicts, the
recommendation text (winner vs "no model clears"), and the mixed-scenario-count
guard. Also covers the CLI round-trip via ``main`` against tmp files.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from spikes.route_engine_llm.compare import build_compare_md, main

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path


# --------------------------------------------------------------------------- #
# Synthetic payload builders (mirror report.build_results_json's schema)
# --------------------------------------------------------------------------- #


def _record(
    name: str,
    *,
    passed: bool,
    shape: str,
    pax: int,
    ordering: str = "forward",
    has_return: bool = False,
    has_hotels: bool = False,
    failure_category: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "passed": passed,
        "failureCategory": None if passed else failure_category,
        "error": None,
        "axes": {
            "shape": shape,
            "pax": pax,
            "ordering": ordering,
            "hasReturn": has_return,
            "hasHotels": has_hotels,
        },
        "fragmentLatencies": [0.1],
        "latency": {"p50": 0.1, "p95": 0.1, "mean": 0.1},
        "usage": {
            "inputTokens": 100,
            "outputTokens": 10,
            "cacheCreationInputTokens": 0,
            "cacheReadInputTokens": 0,
        },
        "costUsd": 0.001,
    }


def _payload(
    model: str,
    records: list[dict[str, Any]],
    *,
    mean_latency: float,
    total_cost: float,
    accuracy_bar: float = 0.99,
    p50: float = 0.0,
    p95: float = 0.0,
) -> dict[str, Any]:
    passed = sum(1 for r in records if r["passed"])
    total = len(records)
    buckets: dict[str, int] = {}
    for record in records:
        if record["passed"]:
            continue
        bucket = record["failureCategory"] or "engine_error"
        buckets[bucket] = buckets.get(bucket, 0) + 1
    return {
        "model": model,
        "modelId": f"us.anthropic.claude-{model}",
        "startedAt": "2026-05-26T12:00:00+00:00",
        "pricing": {"asOf": "2026-05-26", "source": "https://example/pricing"},
        "accuracyBar": accuracy_bar,
        "summary": {
            "scenarios": total,
            "passed": passed,
            "accuracy": passed / total if total else 0.0,
            "totalCostUsd": total_cost,
            "latency": {"p50": p50, "p95": p95, "mean": mean_latency},
            "failureBuckets": buckets,
        },
        "scenarios": records,
    }


def _records_all_pass() -> list[dict[str, Any]]:
    """Four scenarios spanning two shapes / two pax values, all passing."""
    return [
        _record("000-straight-1p-forward", passed=True, shape="straight", pax=1),
        _record("001-straight-2p-forward", passed=True, shape="straight", pax=2),
        _record(
            "064-circle-1p-forward-hotels",
            passed=True,
            shape="circle",
            pax=1,
            has_hotels=True,
        ),
        _record(
            "065-circle-2p-forward-return",
            passed=True,
            shape="circle",
            pax=2,
            has_return=True,
        ),
    ]


def _records_with_failures() -> list[dict[str, Any]]:
    """Same four axes, but two scenarios fail (one per bucket)."""
    return [
        _record("000-straight-1p-forward", passed=True, shape="straight", pax=1),
        _record(
            "001-straight-2p-forward",
            passed=False,
            shape="straight",
            pax=2,
            failure_category="final_mismatch",
        ),
        _record(
            "064-circle-1p-forward-hotels",
            passed=True,
            shape="circle",
            pax=1,
            has_hotels=True,
        ),
        _record(
            "065-circle-2p-forward-return",
            passed=False,
            shape="circle",
            pax=2,
            has_return=True,
            failure_category="identity_violation",
        ),
    ]


# --------------------------------------------------------------------------- #
# Guard: needs >= 2 runs
# --------------------------------------------------------------------------- #


def test_build_compare_md_requires_two_runs() -> None:
    single = _payload("opus", _records_all_pass(), mean_latency=1.0, total_cost=0.5)
    with pytest.raises(ValueError, match="at least two runs"):
        build_compare_md([single])


# --------------------------------------------------------------------------- #
# Headline table
# --------------------------------------------------------------------------- #


def test_headline_table_rows_per_model() -> None:
    opus = _payload("opus", _records_all_pass(), mean_latency=2.0, total_cost=0.9)
    haiku = _payload(
        "haiku", _records_with_failures(), mean_latency=0.5, total_cost=0.1
    )
    md = build_compare_md([opus, haiku])
    # opus: 4/4 = 100% clears bar; haiku: 2/4 = 50% does not.
    assert "| opus | 100.0% | 4/4 |" in md
    assert "| haiku | 50.0% | 2/4 |" in md
    # Cost and latency render in the row.
    assert "$0.900000" in md
    assert "2.000s" in md
    # Bar column: opus yes, haiku no.
    assert "Route-accuracy bar: **>=99.0%**" in md


# --------------------------------------------------------------------------- #
# Per-axis tables, models as columns
# --------------------------------------------------------------------------- #


def test_per_axis_tables_models_as_columns() -> None:
    opus = _payload("opus", _records_all_pass(), mean_latency=2.0, total_cost=0.9)
    haiku = _payload(
        "haiku", _records_with_failures(), mean_latency=0.5, total_cost=0.1
    )
    md = build_compare_md([opus, haiku])
    assert "### Shape" in md
    assert "### Travelers" in md
    # Column header lists both models in order.
    assert "| value | opus | haiku |" in md
    # Shape "circle": opus 1/1 pass = 100%, haiku 1/2 = 50%.
    assert "| circle | 100.0% | 50.0% |" in md
    # Travelers "2": opus 100%, haiku 0% (both 2p scenarios fail for haiku).
    assert "| 2 | 100.0% | 0.0% |" in md


def test_per_axis_blank_cell_when_model_missing_axis_value() -> None:
    # opus exercises a star scenario haiku never ran -> blank cell for haiku.
    opus_records = [
        *_records_all_pass(),
        _record(
            "128-star-3p-bisect", passed=True, shape="star", pax=3, ordering="bisect"
        ),
    ]
    opus = _payload("opus", opus_records, mean_latency=2.0, total_cost=0.9)
    haiku = _payload("haiku", _records_all_pass(), mean_latency=0.5, total_cost=0.1)
    md = build_compare_md([opus, haiku])
    # The star row exists; haiku's cell is the em-dash placeholder.
    assert "| star | 100.0% | — |" in md
    # Travelers axis: pax 3 only in opus.
    assert "| 3 | 100.0% | — |" in md


# --------------------------------------------------------------------------- #
# Failure-bucket comparison
# --------------------------------------------------------------------------- #


def test_failure_bucket_counts_per_model() -> None:
    opus = _payload("opus", _records_all_pass(), mean_latency=2.0, total_cost=0.9)
    haiku = _payload(
        "haiku", _records_with_failures(), mean_latency=0.5, total_cost=0.1
    )
    md = build_compare_md([opus, haiku])
    assert "| bucket | opus | haiku |" in md
    # opus has zero failures; haiku has one of each bucket.
    assert "| final_mismatch | 0 | 1 |" in md
    assert "| identity_violation | 0 | 1 |" in md


def test_no_failures_across_models_renders_clean_note() -> None:
    opus = _payload("opus", _records_all_pass(), mean_latency=2.0, total_cost=0.9)
    haiku = _payload("haiku", _records_all_pass(), mean_latency=0.5, total_cost=0.1)
    md = build_compare_md([opus, haiku])
    assert "No failures across any model." in md


# --------------------------------------------------------------------------- #
# Recommendation logic
# --------------------------------------------------------------------------- #


def test_recommendation_names_cheapest_bar_clearer() -> None:
    # Both clear the bar (100%); haiku is cheaper -> suggested v1 engine.
    opus = _payload("opus", _records_all_pass(), mean_latency=2.0, total_cost=0.9)
    haiku = _payload("haiku", _records_all_pass(), mean_latency=0.5, total_cost=0.1)
    md = build_compare_md([opus, haiku])
    assert "clearing the >=99.0% bar" in md
    assert "Suggested v1 engine: **`haiku`**" in md
    assert "among the bar-clearers" in md


def test_recommendation_single_clearer_named() -> None:
    opus = _payload("opus", _records_all_pass(), mean_latency=2.0, total_cost=0.9)
    haiku = _payload(
        "haiku", _records_with_failures(), mean_latency=0.5, total_cost=0.1
    )
    md = build_compare_md([opus, haiku])
    assert "Suggested v1 engine: **`opus`**" in md
    assert "the only model that clears the bar" in md


def test_recommendation_no_model_clears_falls_back_to_dus20() -> None:
    # Neither clears 99%; opus is closest at 50%.
    opus = _payload("opus", _records_with_failures(), mean_latency=2.0, total_cost=0.9)
    haiku_records = [
        _record(
            "000-straight-1p-forward",
            passed=False,
            shape="straight",
            pax=1,
            failure_category="final_mismatch",
        )
    ]
    haiku = _payload("haiku", haiku_records, mean_latency=0.5, total_cost=0.1)
    md = build_compare_md([opus, haiku])
    assert "**No model clears the >=99.0% route-accuracy bar.**" in md
    assert "algorithmic engine spike (DUS-20)" in md
    assert "closest is `opus`" in md


# --------------------------------------------------------------------------- #
# Mixed scenario-count guard
# --------------------------------------------------------------------------- #


def test_mixed_scenario_count_guard_warns() -> None:
    # opus: full-ish 4-scenario run; haiku: 1-scenario probe.
    opus = _payload("opus", _records_all_pass(), mean_latency=2.0, total_cost=0.9)
    haiku_records = [
        _record("000-straight-1p-forward", passed=True, shape="straight", pax=1)
    ]
    haiku = _payload("haiku", haiku_records, mean_latency=0.5, total_cost=0.1)
    md = build_compare_md([opus, haiku])
    assert "uneven scenario coverage" in md.lower() or "Uneven" in md
    assert "opus=4" in md
    assert "haiku=1" in md


def test_equal_scenario_counts_no_guard() -> None:
    opus = _payload("opus", _records_all_pass(), mean_latency=2.0, total_cost=0.9)
    haiku = _payload("haiku", _records_all_pass(), mean_latency=0.5, total_cost=0.1)
    md = build_compare_md([opus, haiku])
    assert "uneven scenario coverage" not in md.lower()


# --------------------------------------------------------------------------- #
# CLI round-trip
# --------------------------------------------------------------------------- #


def test_main_writes_compare_md(tmp_path: Path) -> None:
    opus = _payload("opus", _records_all_pass(), mean_latency=2.0, total_cost=0.9)
    haiku = _payload("haiku", _records_all_pass(), mean_latency=0.5, total_cost=0.1)
    opus_path = tmp_path / "opus.json"
    haiku_path = tmp_path / "haiku.json"
    opus_path.write_text(json.dumps(opus), encoding="utf-8")
    haiku_path.write_text(json.dumps(haiku), encoding="utf-8")
    out = tmp_path / "compare.md"

    code = main([str(opus_path), str(haiku_path), "-o", str(out)])
    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert "# Cross-model engine comparison" in text
    assert "| opus |" in text and "| haiku |" in text


def test_main_rejects_single_input(tmp_path: Path) -> None:
    opus = _payload("opus", _records_all_pass(), mean_latency=2.0, total_cost=0.9)
    opus_path = tmp_path / "opus.json"
    opus_path.write_text(json.dumps(opus), encoding="utf-8")
    # argparse nargs="+" allows one; main() enforces the >=2 floor.
    assert main([str(opus_path), "-o", str(tmp_path / "x.md")]) == 2


def test_main_missing_file_errors(tmp_path: Path) -> None:
    opus = _payload("opus", _records_all_pass(), mean_latency=2.0, total_cost=0.9)
    opus_path = tmp_path / "opus.json"
    opus_path.write_text(json.dumps(opus), encoding="utf-8")
    assert main([str(opus_path), str(tmp_path / "nope.json")]) == 2
