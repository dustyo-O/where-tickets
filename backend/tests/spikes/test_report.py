"""Offline tests for report aggregation + axis parsing (synthetic results).

No network, no ``anthropic``, no AWS. Asserts: scenario-name axis parsing,
headline accuracy, per-axis breakdown, nearest-rank latency percentiles,
failure-bucket counts, the results.json schema, and that cost flows through.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from spikes.route_engine_llm.bedrock_client import Usage
from spikes.route_engine_llm.report import (
    RunMeta,
    ScenarioResult,
    build_report_md,
    build_results_json,
    parse_axes,
)
from spikes.route_engine_llm.scoring import FailureCategory


def _meta() -> RunMeta:
    return RunMeta(
        model="haiku",
        model_id="us.anthropic.claude-haiku-4-5",
        started_at=datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC),
        pricing_as_of="2026-05-26",
        pricing_source="https://aws.amazon.com/bedrock/pricing/",
    )


# --------------------------------------------------------------------------- #
# Axis parsing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("name", "shape", "pax", "ordering", "has_return", "has_hotels"),
    [
        ("000-straight-1p-forward", "straight", 1, "forward", False, False),
        ("004-straight-1p-forward-hotels", "straight", 1, "forward", False, True),
        ("008-straight-1p-forward-return", "straight", 1, "forward", True, False),
        (
            "012-straight-1p-forward-return-hotels",
            "straight",
            1,
            "forward",
            True,
            True,
        ),
        (
            "099-circle-4p-seeded-shuffle-return-hotels",
            "circle",
            4,
            "seeded-shuffle",
            True,
            True,
        ),
        ("130-star-3p-bisect", "star", 3, "bisect", False, False),
    ],
)
def test_parse_axes_handles_full_grammar(
    name: str,
    shape: str,
    pax: int,
    ordering: str,
    has_return: bool,
    has_hotels: bool,
) -> None:
    axes = parse_axes(name)
    assert axes.shape == shape
    assert axes.pax == pax
    assert axes.ordering == ordering
    assert axes.has_return is has_return
    assert axes.has_hotels is has_hotels


def test_parse_axes_rejects_bad_name() -> None:
    with pytest.raises(ValueError, match="corpus grammar"):
        parse_axes("not-a-scenario")


# --------------------------------------------------------------------------- #
# Fixtures: a small synthetic result set with a known shape
# --------------------------------------------------------------------------- #


def _results() -> list[ScenarioResult]:
    return [
        # straight: pass
        ScenarioResult(
            name="000-straight-1p-forward",
            passed=True,
            fragment_latencies=[0.1, 0.2, 0.3, 0.4],
            usage=Usage(input_tokens=100, output_tokens=10),
            cost_usd=0.001,
        ),
        # straight: fail final-match
        ScenarioResult(
            name="001-straight-2p-reverse",
            passed=False,
            failure_category=str(FailureCategory.FINAL_MISMATCH),
            fragment_latencies=[1.0],
            usage=Usage(input_tokens=200, output_tokens=20),
            cost_usd=0.002,
        ),
        # circle: fail identity
        ScenarioResult(
            name="064-circle-1p-forward",
            passed=False,
            failure_category=str(FailureCategory.IDENTITY_VIOLATION),
            fragment_latencies=[0.5, 0.5],
            usage=Usage(input_tokens=300, output_tokens=30),
            cost_usd=0.003,
        ),
        # circle: engine error (no category)
        ScenarioResult(
            name="065-circle-2p-reverse",
            passed=False,
            error="dangling op",
            fragment_latencies=[0.2],
            usage=Usage(input_tokens=50, output_tokens=5),
            cost_usd=0.0005,
        ),
        # star: pass
        ScenarioResult(
            name="128-star-1p-forward",
            passed=True,
            fragment_latencies=[0.05],
            usage=Usage(input_tokens=80, output_tokens=8),
            cost_usd=0.0008,
        ),
    ]


# --------------------------------------------------------------------------- #
# results.json
# --------------------------------------------------------------------------- #


def test_results_json_headline_accuracy_and_cost() -> None:
    payload = json.loads(build_results_json(_meta(), _results()))
    summary = payload["summary"]
    assert summary["scenarios"] == 5
    assert summary["passed"] == 2
    assert summary["accuracy"] == pytest.approx(2 / 5)
    assert summary["totalCostUsd"] == pytest.approx(
        0.001 + 0.002 + 0.003 + 0.0005 + 0.0008
    )


def test_results_json_failure_buckets_count_only() -> None:
    payload = json.loads(build_results_json(_meta(), _results()))
    assert payload["summary"]["failureBuckets"] == {
        "final_mismatch": 1,
        "identity_violation": 1,
        "engine_error": 1,
    }


def test_results_json_per_scenario_record_carries_axes_and_usage() -> None:
    payload = json.loads(build_results_json(_meta(), _results()))
    by_name = {s["name"]: s for s in payload["scenarios"]}
    circle = by_name["064-circle-1p-forward"]
    assert circle["axes"] == {
        "shape": "circle",
        "pax": 1,
        "ordering": "forward",
        "hasReturn": False,
        "hasHotels": False,
    }
    assert circle["failureCategory"] == "identity_violation"
    assert circle["usage"]["inputTokens"] == 300
    # Two equal 0.5s latencies -> every percentile is 0.5.
    assert circle["latency"] == {"p50": 0.5, "p95": 0.5, "mean": 0.5}


def test_results_json_overall_latency_percentiles_nearest_rank() -> None:
    # Latencies pooled across all scenarios:
    # [0.1,0.2,0.3,0.4, 1.0, 0.5,0.5, 0.2, 0.05] -> sorted:
    # [0.05,0.1,0.2,0.2,0.3,0.4,0.5,0.5,1.0] (n=9)
    payload = json.loads(build_results_json(_meta(), _results()))
    latency = payload["summary"]["latency"]
    # p50: rank ceil(0.5*9)=5 -> index 4 -> 0.3
    assert latency["p50"] == pytest.approx(0.3)
    # p95: rank ceil(0.95*9)=9 -> index 8 -> 1.0
    assert latency["p95"] == pytest.approx(1.0)
    assert latency["mean"] == pytest.approx(
        (0.05 + 0.1 + 0.2 + 0.2 + 0.3 + 0.4 + 0.5 + 0.5 + 1.0) / 9
    )


def test_results_json_metadata_round_trips() -> None:
    payload = json.loads(build_results_json(_meta(), _results()))
    assert payload["model"] == "haiku"
    assert payload["modelId"] == "us.anthropic.claude-haiku-4-5"
    assert payload["accuracyBar"] == pytest.approx(0.99)
    assert payload["pricing"]["asOf"] == "2026-05-26"
    assert payload["startedAt"].startswith("2026-05-26T12:00:00")


def test_results_json_empty_run_is_safe() -> None:
    payload = json.loads(build_results_json(_meta(), []))
    assert payload["summary"]["scenarios"] == 0
    assert payload["summary"]["accuracy"] == 0.0
    assert payload["summary"]["failureBuckets"] == {}


# --------------------------------------------------------------------------- #
# report.md
# --------------------------------------------------------------------------- #


def test_report_md_headline_and_axis_breakdown() -> None:
    md = build_report_md(_meta(), _results())
    # Headline: 2/5 = 40.0% and does not clear the 99% bar.
    assert "40.0%" in md
    assert "(2/5 scenarios passed" in md
    assert "Clears the >=99.0% bar: **no**" in md
    # Per-axis: shape breakdown rows present.
    assert "### Shape" in md
    # straight: 1 of 2 pass = 50.0%; circle: 0 of 2; star: 1 of 1 = 100.0%.
    assert "| circle | 0 | 2 | 0.0% |" in md
    assert "| star | 1 | 1 | 100.0% |" in md


def test_report_md_failure_buckets_counts_only() -> None:
    md = build_report_md(_meta(), _results())
    assert "## Failure buckets (counts only)" in md
    assert "| engine_error | 1 |" in md
    assert "| final_mismatch | 1 |" in md
    assert "| identity_violation | 1 |" in md
    # Counts only: the error message text never appears in the report.
    assert "dangling op" not in md


def test_report_md_clean_run_reports_no_failures() -> None:
    clean = [
        ScenarioResult(
            name="000-straight-1p-forward",
            passed=True,
            fragment_latencies=[0.1],
            cost_usd=0.0,
        )
    ]
    md = build_report_md(_meta(), clean)
    assert "100.0%" in md
    assert "Clears the >=99.0% bar: **yes**" in md
    assert "No failures." in md


def test_report_md_lists_per_scenario_cost_and_latency() -> None:
    md = build_report_md(_meta(), _results())
    assert "## Per-scenario latency & cost" in md
    # The passing straight scenario shows its p50 over [0.1,0.2,0.3,0.4]:
    # rank ceil(0.5*4)=2 -> index 1 -> 0.2.
    assert "| 000-straight-1p-forward | pass | 0.200s |" in md
