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
REAL_FIXTURE_DIR = (
    REPO_ROOT
    / "corpus"
    / "pdf"
    / "layer1"
    / "scenarios"
    / "001-air-1leg-1pax-paris-lisbon"
)


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
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke the runner with ``tmp_path`` as ``WT_REPO_ROOT`` and ``PYTHONPATH``."""
    env = {
        **os.environ,
        "WT_REPO_ROOT": str(tmp_path),
        "PYTHONPATH": str(tmp_path),
    }
    if extra_env:
        env.update(extra_env)
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
        "extractor not wired: install per AI Document Understanding spec" in proc.stdout
    )
    # Skipped summary: 0/2 for Layer 1, 0/0 for Layer 2, 0/2 overall.
    assert re.search(r"Layer 1 \(synthetic\):\s+0/2 skipped", proc.stdout), proc.stdout
    assert re.search(r"Layer 2 \(real\):\s+0/0 skipped", proc.stdout), proc.stdout
    assert re.search(r"TOTAL:\s+0/2 skipped\s+—\s+extractor not wired", proc.stdout), (
        proc.stdout
    )
    # No FAILED: block when nothing was actually compared.
    assert "FAILED:" not in proc.stdout
    # No path-mix or DIAGNOSTIC blocks when nothing was extracted.
    assert "path:" not in proc.stdout
    assert "DIAGNOSTIC" not in proc.stdout


# --------------------------------------------------------------------------- #
# Sub-task 5/6 tests: path-mix summary + DIAGNOSTIC accidental-vision block.
# --------------------------------------------------------------------------- #


def _with_path(payload: dict[str, Any], extraction_path: str) -> dict[str, Any]:
    """Return a copy of ``payload`` tagged with the given ``extraction_path``."""
    tagged = dict(payload)
    tagged["extraction_path"] = extraction_path
    return tagged


def test_path_mix_summary_appears_on_layer_lines(corpus_tree: Path) -> None:
    """Layer 1 line shows ``path: text=1 vision=1``; TOTAL has no path-mix."""
    _write_stub(
        corpus_tree,
        {
            "001-pass": _with_path(_air_ticket_payload(), "text"),
            "002-hotel": _with_path(_hotel_payload(), "vision"),
        },
    )

    proc = _run_runner(corpus_tree)

    assert proc.returncode == 0, f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
    # Locate the Layer 1 line and assert path-mix tokens, with no `unknown=`.
    layer1_line = next(
        line
        for line in proc.stdout.splitlines()
        if line.startswith("Layer 1 (synthetic):")
    )
    assert "path: text=1 vision=1" in layer1_line, layer1_line
    assert "unknown" not in layer1_line, layer1_line
    # TOTAL line has no path-mix segment.
    total_line = next(
        line for line in proc.stdout.splitlines() if line.startswith("TOTAL:")
    )
    assert "path:" not in total_line, total_line


def test_unknown_path_category_appears_when_extraction_path_missing(
    corpus_tree: Path,
) -> None:
    """A response omitting ``extraction_path`` is counted as ``unknown``."""
    _write_stub(
        corpus_tree,
        {
            # No extraction_path on either response.
            "001-pass": _air_ticket_payload(),
            "002-hotel": _with_path(_hotel_payload(), "text"),
        },
    )

    proc = _run_runner(corpus_tree)

    assert proc.returncode == 0, f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
    layer1_line = next(
        line
        for line in proc.stdout.splitlines()
        if line.startswith("Layer 1 (synthetic):")
    )
    # 1 text, 1 unknown (the air-ticket scenario), in that exact order.
    assert "path: text=1 unknown=1" in layer1_line, layer1_line
    assert "vision" not in layer1_line, layer1_line


def test_diagnostic_lists_text_pdf_routed_to_vision(corpus_tree: Path) -> None:
    """``pdf_kind=text`` + ``extraction_path=vision`` → DIAGNOSTIC entry, exit 0."""
    _write_stub(
        corpus_tree,
        {
            # Air ticket: text PDF, extractor (correctly) returns text path.
            "001-pass": _with_path(_air_ticket_payload(), "text"),
            # Hotel: expected pdf_kind=text, but extractor fell back to vision.
            "002-hotel": _with_path(_hotel_payload(), "vision"),
        },
    )

    proc = _run_runner(corpus_tree)

    # Still a PASS run — diagnostic does not flip exit code.
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
    assert "DIAGNOSTIC (non-failing):" in proc.stdout, proc.stdout

    diagnostic_block = proc.stdout.split("DIAGNOSTIC (non-failing):", 1)[1]
    # The hotel scenario appears with the layer prefix and the canonical phrasing.
    assert "L1 002-hotel/document.pdf" in diagnostic_block, diagnostic_block
    assert "pdf_kind=text but extractor fell back to vision" in diagnostic_block
    # The text-path scenario should NOT appear in the diagnostic block.
    assert "001-pass" not in diagnostic_block, diagnostic_block


# --------------------------------------------------------------------------- #
# Slice 6: Layer 2 discovery + leak guard.
# --------------------------------------------------------------------------- #


VALIDATE_PATH = REPO_ROOT / "corpus" / "pdf" / "validate.py"


def _write_layer2_scenario(
    layer2_root: Path,
    trip_slug: str,
    pdf_stem: str,
    expected: dict[str, Any],
    source_pdf: Path,
) -> None:
    """Create ``<layer2_root>/<trip>/<stem>.pdf`` + sibling expected-fields JSON."""
    trip_dir = layer2_root / trip_slug
    trip_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(source_pdf, trip_dir / f"{pdf_stem}.pdf")
    payload = dict(expected)
    payload["scenario_id"] = f"{trip_slug}/{pdf_stem}"
    (trip_dir / f"{pdf_stem}.expected-fields.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True)
    )


def test_layer_2_trip_is_discovered_and_reported(corpus_tree: Path) -> None:
    """A Layer 2 trip dropped into ``corpus/pdf/layer2/`` is discovered and reported.

    Builds the same tree as the other tests (one PASS-stub Layer 1 scenario)
    plus a single Layer 2 PDF + sibling JSON under
    ``corpus/pdf/layer2/porto-trip/01-hotel-booking.*``. Sets ``WT_LAYER2_ROOT``
    so the runner discovers the tempdir's layer2 even if a contributor wires
    it independently from ``WT_REPO_ROOT``.
    """
    layer2_root = corpus_tree / "corpus" / "pdf" / "layer2"
    # Reuse the same fixture PDF the other tests use; the runner doesn't read
    # the bytes, only the path layout + sibling JSON.
    source_pdf = REAL_FIXTURE_DIR / "document.pdf"
    _write_layer2_scenario(
        layer2_root,
        trip_slug="porto-trip",
        pdf_stem="01-hotel-booking",
        expected=_hotel_payload(),
        source_pdf=source_pdf,
    )

    # Replace the second Layer 1 scenario so the tree is clean: one Layer 1
    # PASS scenario + one Layer 2 PASS scenario.
    shutil.rmtree(corpus_tree / "corpus" / "pdf" / "layer1" / "scenarios" / "002-hotel")

    _write_stub(
        corpus_tree,
        {
            "001-pass": _with_path(_air_ticket_payload(), "text"),
            # Layer 2 stub key matches the trip directory name (the PDF's parent).
            "porto-trip": _with_path(_hotel_payload(), "text"),
        },
    )

    proc = _run_runner(
        corpus_tree,
        extra_env={"WT_LAYER2_ROOT": str(layer2_root)},
    )

    assert proc.returncode == 0, f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
    assert re.search(r"Layer 1 \(synthetic\):\s+1/1 PASS\s+\(100\.0%\)", proc.stdout), (
        proc.stdout
    )
    assert re.search(r"Layer 2 \(real\):\s+1/1 PASS\s+\(100\.0%\)", proc.stdout), (
        proc.stdout
    )
    assert re.search(r"TOTAL:\s+2/2 PASS\s+\(100\.0%\)", proc.stdout), proc.stdout

    # The Layer 2 line carries its own path-mix segment.
    layer2_line = next(
        line for line in proc.stdout.splitlines() if line.startswith("Layer 2 (real):")
    )
    assert "path: text=1" in layer2_line, layer2_line
    # No FAILED: block on an all-pass run.
    assert "FAILED:" not in proc.stdout


def test_layer_2_leak_guard_catches_top_level_tracked_pdf(tmp_path: Path) -> None:
    """Stage a fake PDF directly at ``corpus/pdf/layer2/`` → validator exits non-zero.

    DUS-31 Slice 8: the leak guard now distinguishes between TOP-LEVEL files
    (still forbidden — only ``.gitkeep`` is allowed) and files inside a
    generator-emitted TRIP DIRECTORY (allowed; layer-2 trips are intentionally
    committed). This test stages a top-level leaker and asserts the guard
    fires; the trip-dir allowance is covered in
    :func:`test_layer_2_leak_guard_allows_trip_directory_files`.
    """
    # Build the minimum tree the validator needs: schema + layer1 + layer2.
    pdf_root = tmp_path / "corpus" / "pdf"
    (pdf_root / "schema").mkdir(parents=True)
    (pdf_root / "layer1" / "scenarios").mkdir(parents=True)
    (pdf_root / "layer2").mkdir(parents=True)

    shutil.copy(REAL_SCHEMA, pdf_root / "schema" / "expected-fields.schema.json")

    # Init a fresh repo so `git ls-files` has an index to read.
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    # Track the .gitkeep placeholder + a fake leaked top-level PDF.
    (pdf_root / "layer2" / ".gitkeep").touch()
    leaked_pdf = pdf_root / "layer2" / "rogue-top-level.pdf"
    leaked_pdf.write_bytes(b"%PDF-1.4 fake bytes")
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "add",
            "corpus/pdf/layer2/.gitkeep",
            str(leaked_pdf),
        ],
        check=True,
    )

    env = {**os.environ, "WT_REPO_ROOT": str(tmp_path)}
    proc = subprocess.run(
        [
            "uv",
            "run",
            "--python",
            "3.12",
            "--with",
            "jsonschema",
            "python",
            str(VALIDATE_PATH),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )

    assert proc.returncode == 1, f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
    assert "Layer 2 leak guard failed" in proc.stdout, proc.stdout
    assert (
        "layer2-leak: corpus/pdf/layer2/rogue-top-level.pdf "
        "is tracked at the top of corpus/pdf/layer2/"
    ) in proc.stdout, proc.stdout
    # Remediation hint points at `git rm --cached` with the offending path.
    assert (
        "git rm --cached corpus/pdf/layer2/rogue-top-level.pdf"
        in proc.stdout
    ), proc.stdout
    # The allowed .gitkeep entry is NOT flagged.
    assert ".gitkeep is tracked" not in proc.stdout


def test_layer_2_leak_guard_allows_trip_directory_files(tmp_path: Path) -> None:
    """Files inside a generator-emitted trip directory don't trip the guard.

    DUS-31 Slice 8 acceptance: integration trips ship PDFs + sibling
    expected-fields.json files under
    ``corpus/pdf/layer2/<trip-slug>/<NN>-<docname>.{pdf,expected-fields.json}``.
    The leak guard recognises trip subdirectories and lets their contents
    through; only top-level files trip the guard.
    """
    pdf_root = tmp_path / "corpus" / "pdf"
    (pdf_root / "schema").mkdir(parents=True)
    (pdf_root / "layer1" / "scenarios").mkdir(parents=True)
    (pdf_root / "layer2").mkdir(parents=True)

    shutil.copy(REAL_SCHEMA, pdf_root / "schema" / "expected-fields.schema.json")

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    (pdf_root / "layer2" / ".gitkeep").touch()
    trip_pdf = pdf_root / "layer2" / "demo-trip" / "01-air-leg-1.pdf"
    trip_pdf.parent.mkdir(parents=True)
    # Use a real (tiny) PDF byte sequence so the validator's PyMuPDF sanity
    # check doesn't blow up — but the file's sibling expected-fields.json
    # references the file's stem so the discovery walks it.
    import pymupdf  # noqa: PLC0415

    blank = pymupdf.open()
    blank.new_page(width=595, height=842)
    blank.save(str(trip_pdf))
    blank.close()
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "add",
            "corpus/pdf/layer2/.gitkeep",
            str(trip_pdf),
        ],
        check=True,
    )

    env = {**os.environ, "WT_REPO_ROOT": str(tmp_path)}
    proc = subprocess.run(
        [
            "uv",
            "run",
            "--python",
            "3.12",
            "--with",
            "jsonschema",
            "--with",
            "pymupdf",
            "python",
            str(VALIDATE_PATH),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )

    # The leak guard must NOT fire. The validator may still fail on the
    # coverage band (the layer-1 tree is empty in this fixture), but the
    # layer-2 leak block specifically must be absent.
    assert "Layer 2 leak guard failed" not in proc.stdout, proc.stdout
    assert "layer2-leak:" not in proc.stdout, proc.stdout
