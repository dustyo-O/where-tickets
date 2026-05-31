"""End-to-end tests for ``corpus/pdf/runner.py``.

The runner is exercised via subprocess (it's a CLI script) against a tiny
tempdir corpus tree built per test. ``WT_REPO_ROOT`` overrides the runner's
root anchor so it discovers the tempdir scenarios instead of the real corpus.

A small stub extractor is dropped into the tempdir; the runner imports it via
``--extractor-import-path test_stub.extract_pdf``. The stub reads its
canned responses from ``stub_responses.json`` sitting next to it, keyed by
the scenario directory name (e.g. ``"001-pass"``), so each test can dictate
the per-scenario extracted payload without touching code.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER_PATH = REPO_ROOT / "corpus" / "pdf" / "runner.py"
REAL_SCHEMA = REPO_ROOT / "corpus" / "pdf" / "schema" / "expected-fields.schema.json"
# Slice 3 replaced the hand-authored Slice 1 fixture with the generator's
# first matrix scenario; this test only needs *any* committed PDF whose bytes
# can be copied into the per-test tempdir, so the path is fine to swap.
REAL_FIXTURE_DIR = REPO_ROOT / "corpus" / "pdf" / "layer1" / "scenarios" / "001-air-1leg-1pax-paris-lisbon"


# --------------------------------------------------------------------------- #
# Canonical extracted payloads (mirror the Slice 1 hand-authored fixture).
# --------------------------------------------------------------------------- #


def _air_ticket_payload() -> dict[str, Any]:
    """The reference ExtractedFields shape for the Paris→Lisbon fixture."""
    return {
        "document_type": "air_ticket",
        "cities": ["Paris", "Lisbon"],
        "stations": [
            {
                "city": "Paris",
                "kind": "airport",
                "identifier": "CDG",
                "departure_datetime": "2027-04-12T08:30:00",
            },
            {
                "city": "Lisbon",
                "kind": "airport",
                "identifier": "LIS",
                "arrival_datetime": "2027-04-12T10:45:00",
            },
        ],
        "accommodations": [],
        "venues": [],
        "travelers": ["Alice Example"],
        "prices": [{"amount": 129.50, "currency": "EUR"}],
        "qr_codes": ["FIXTURE-QR-001"],
        "pdf_kind": "text",
    }


def _hotel_payload() -> dict[str, Any]:
    """A second scenario shape: hotel booking (one city, one accommodation)."""
    return {
        "document_type": "hotel_booking",
        "cities": ["Madrid"],
        "stations": [],
        "accommodations": [
            {
                "city": "Madrid",
                "kind": "hotel",
                "identifier": "Hotel Centro",
                "check_in_datetime": "2027-05-01T15:00:00",
                "check_out_datetime": "2027-05-03T11:00:00",
            }
        ],
        "venues": [],
        "travelers": ["Bob Example"],
        "prices": [{"amount": 240.0, "currency": "EUR"}],
        "qr_codes": ["HOTEL-QR-002"],
        "pdf_kind": "text",
    }


# --------------------------------------------------------------------------- #
# Stub extractor source — written to ``<tmp>/test_stub.py`` per fixture.
# --------------------------------------------------------------------------- #


STUB_EXTRACTOR_SOURCE = '''\
"""Stub extractor used by backend/tests/corpus/test_pdf_runner.py.

Reads canned per-scenario responses from a sibling ``stub_responses.json``
file (a ``dict[str, dict]`` keyed by the parent directory name of the PDF —
i.e. the scenario slug), and returns the matching ExtractedFields-shaped
dict on every ``extract_pdf(pdf_path)`` call.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_RESPONSES_PATH = Path(__file__).resolve().parent / "stub_responses.json"
_RESPONSES: dict[str, dict[str, Any]] = json.loads(_RESPONSES_PATH.read_text())


def extract_pdf(pdf_path: Path) -> dict[str, Any]:
    return _RESPONSES[pdf_path.parent.name]
'''


# --------------------------------------------------------------------------- #
# Tree builder.
# --------------------------------------------------------------------------- #


def _write_scenario(
    scenarios_root: Path,
    slug: str,
    expected: dict[str, Any],
    source_pdf: Path,
) -> None:
    """Create ``<scenarios_root>/<slug>/{document.pdf,expected-fields.json}``."""
    scenario_dir = scenarios_root / slug
    scenario_dir.mkdir(parents=True)
    shutil.copy(source_pdf, scenario_dir / "document.pdf")
    payload = dict(expected)
    payload["scenario_id"] = slug
    (scenario_dir / "expected-fields.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True)
    )


@pytest.fixture
def corpus_tree(tmp_path: Path) -> Path:
    """Build a minimal ``<tmp>/corpus/pdf/{schema,layer1/scenarios,layer2}`` tree.

    Two Layer 1 scenarios are created:
    - ``001-pass``  — paired with the canonical air-ticket expected fields.
    - ``002-hotel`` — paired with the canonical hotel expected fields.

    Layer 2 is created empty so the runner sees ``0/0`` for it. The real
    schema and the real fixture PDF are copied in (file contents don't
    matter to the runner; only paths and the sibling JSON do).
    """
    pdf_root = tmp_path / "corpus" / "pdf"
    (pdf_root / "schema").mkdir(parents=True)
    (pdf_root / "layer1" / "scenarios").mkdir(parents=True)
    (pdf_root / "layer2").mkdir(parents=True)

    shutil.copy(REAL_SCHEMA, pdf_root / "schema" / "expected-fields.schema.json")

    source_pdf = REAL_FIXTURE_DIR / "document.pdf"
    scenarios_root = pdf_root / "layer1" / "scenarios"
    _write_scenario(scenarios_root, "001-pass", _air_ticket_payload(), source_pdf)
    _write_scenario(scenarios_root, "002-hotel", _hotel_payload(), source_pdf)

    return tmp_path


# --------------------------------------------------------------------------- #
# Subprocess helpers.
# --------------------------------------------------------------------------- #


def _write_stub(tmp_path: Path, responses: dict[str, dict[str, Any]]) -> None:
    """Drop ``test_stub.py`` + ``stub_responses.json`` into the tempdir."""
    (tmp_path / "test_stub.py").write_text(STUB_EXTRACTOR_SOURCE)
    (tmp_path / "stub_responses.json").write_text(json.dumps(responses))


def _run_runner(
    tmp_path: Path,
    extractor_import_path: str = "test_stub.extract_pdf",
) -> subprocess.CompletedProcess[str]:
    """Invoke the runner with ``tmp_path`` as ``WT_REPO_ROOT`` and ``PYTHONPATH``."""
    env = {
        **os.environ,
        "WT_REPO_ROOT": str(tmp_path),
        "PYTHONPATH": str(tmp_path),
    }
    return subprocess.run(
        [
            sys.executable,
            str(RUNNER_PATH),
            "--extractor-import-path",
            extractor_import_path,
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )


# --------------------------------------------------------------------------- #
# Tests.
# --------------------------------------------------------------------------- #


def test_all_pass_exits_zero_with_full_pass_summary(corpus_tree: Path) -> None:
    """Stub returns the exact expected fields → exit 0, 2/2 PASS on Layer 1."""
    _write_stub(
        corpus_tree,
        {
            "001-pass": _air_ticket_payload(),
            "002-hotel": _hotel_payload(),
        },
    )

    proc = _run_runner(corpus_tree)

    assert proc.returncode == 0, f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
    # Layer 1: 2/2 PASS with 100% accuracy.
    assert re.search(r"Layer 1 \(synthetic\):\s+2/2 PASS\s+\(100\.0%\)", proc.stdout), (
        proc.stdout
    )
    # Layer 2 is empty: 0/0 with n/a accuracy.
    assert re.search(r"Layer 2 \(real\):\s+0/0 PASS\s+\(\s*n/a\s*%\)", proc.stdout), (
        proc.stdout
    )
    # TOTAL: 2/2 PASS.
    assert re.search(r"TOTAL:\s+2/2 PASS\s+\(100\.0%\)", proc.stdout), proc.stdout
    # No FAILED: block on an all-pass run.
    assert "FAILED:" not in proc.stdout


def test_field_mismatch_exits_one_with_failed_block(corpus_tree: Path) -> None:
    """A wrong ``cities`` value on one scenario → exit 1 + ``FAILED:`` block."""
    wrong_air = _air_ticket_payload()
    wrong_air["cities"] = ["Paris"]  # missing "Lisbon"
    _write_stub(
        corpus_tree,
        {
            "001-pass": wrong_air,
            "002-hotel": _hotel_payload(),
        },
    )

    proc = _run_runner(corpus_tree)

    assert proc.returncode == 1, f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
    # Summary still prints, with 1/2 PASS at Layer 1.
    assert re.search(r"Layer 1 \(synthetic\):\s+1/2 PASS", proc.stdout), proc.stdout
    # FAILED: block names the mismatching scenario and field.
    assert "FAILED:" in proc.stdout
    assert "L1 001-pass/document.pdf" in proc.stdout
    # The cities diff line: name + expected (sorted) + actual (sorted).
    assert re.search(
        r"cities:\s+expected\s+\[.*Lisbon.*Paris.*\]\s+actual\s+\[.*Paris.*\]",
        proc.stdout,
    ), proc.stdout
    # The passing scenario does NOT appear under FAILED:.
    failed_block = proc.stdout.split("FAILED:", 1)[1]
    assert "002-hotel" not in failed_block


def test_extractor_not_wired_prints_banner_and_exits_zero(corpus_tree: Path) -> None:
    """A bogus ``--extractor-import-path`` → not-wired banner + exit 0."""
    # We deliberately do NOT write a stub — and we point the runner at a
    # module that cannot be imported.
    proc = _run_runner(
        corpus_tree,
        extractor_import_path="definitely.not.a.real.module.extract_pdf",
    )

    assert proc.returncode == 0, f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
    assert (
        "extractor not wired: install per AI Document Understanding spec"
        in proc.stdout
    )
    # Skipped summary: 0/2 for Layer 1, 0/0 for Layer 2, 0/2 overall.
    assert re.search(r"Layer 1 \(synthetic\):\s+0/2 skipped", proc.stdout), proc.stdout
    assert re.search(r"Layer 2 \(real\):\s+0/0 skipped", proc.stdout), proc.stdout
    assert re.search(
        r"TOTAL:\s+0/2 skipped\s+—\s+extractor not wired", proc.stdout
    ), proc.stdout
    # No FAILED: block when nothing was actually compared.
    assert "FAILED:" not in proc.stdout
