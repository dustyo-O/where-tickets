"""End-to-end tests for ``corpus/pdf/validate.py``'s coverage assertions.

The validator is a CLI; we exercise it via subprocess against a tmpdir clone
of the real corpus so we can delete categories of scenarios and assert the
coverage check fires with the right message.

Cloning strategy: for speed (the real corpus is ~131 MB of PDFs), each Layer
1 scenario directory is **symlinked** into the tempdir. The validator only
reads files, never mutates them, so symlinks are equivalent to copies. The
schema file is real-copied (cheap) and ``layer2/`` is created empty.
``WT_REPO_ROOT`` redirects validator discovery at the tempdir tree.

The drift + sanity + per-file checks all fire too; the assertions in this
module look only at the coverage-block lines so a drift failure in a
negative scenario (which is expected: we just deleted scenarios from disk)
doesn't muddy the test signal.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
VALIDATE_PATH = REPO_ROOT / "corpus" / "pdf" / "validate.py"
REAL_SCHEMA = REPO_ROOT / "corpus" / "pdf" / "schema" / "expected-fields.schema.json"
REAL_LAYER1 = REPO_ROOT / "corpus" / "pdf" / "layer1" / "scenarios"
# Slice 10: the cross-schema check loads the engine fragment schema from
# ``corpus/schema/`` (sibling of ``corpus/pdf/``). Clones need it copied in
# too, or the check fails with a source-of-truth-divergence error.
REAL_FRAGMENT_SCHEMA = REPO_ROOT / "corpus" / "schema" / "extracted-fragment.schema.json"


# --------------------------------------------------------------------------- #
# Helpers.
# --------------------------------------------------------------------------- #


def _clone_corpus(tmp_path: Path) -> Path:
    """Build ``<tmp>/corpus/pdf/{schema,layer1/scenarios,layer2}`` from the real corpus.

    Each Layer 1 scenario directory is symlinked (cheap; validator is
    read-only). The schema file is real-copied so the validator can read it
    without following symlinks. ``layer2/`` is created empty.
    """
    pdf_root = tmp_path / "corpus" / "pdf"
    (pdf_root / "schema").mkdir(parents=True)
    scenarios_root = pdf_root / "layer1" / "scenarios"
    scenarios_root.mkdir(parents=True)
    (pdf_root / "layer2").mkdir(parents=True)
    # Engine fragment schema sits at ``corpus/schema/``, sibling of pdf/.
    engine_schema_root = tmp_path / "corpus" / "schema"
    engine_schema_root.mkdir(parents=True)

    shutil.copy(REAL_SCHEMA, pdf_root / "schema" / "expected-fields.schema.json")
    shutil.copy(
        REAL_FRAGMENT_SCHEMA, engine_schema_root / "extracted-fragment.schema.json"
    )

    for scenario_dir in sorted(REAL_LAYER1.iterdir()):
        if scenario_dir.is_dir():
            os.symlink(scenario_dir, scenarios_root / scenario_dir.name)

    return tmp_path


def _run_validator(repo_root: Path) -> subprocess.CompletedProcess[str]:
    """Invoke ``validate.py`` against ``repo_root`` via ``uv run``.

    Matches the dependency bundle ``just test-corpus`` uses (the backend's
    own venv lacks ``jsonschema`` by design), so the subprocess pulls in
    ``jsonschema`` + ``pymupdf`` the same way CI does.
    """
    env = {**os.environ, "WT_REPO_ROOT": str(repo_root)}
    return subprocess.run(
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
        cwd=repo_root,
        check=False,
    )


@pytest.fixture
def cloned_corpus(tmp_path: Path) -> Path:
    """Return a tmpdir with the real corpus mirrored in for the validator."""
    return _clone_corpus(tmp_path)


# --------------------------------------------------------------------------- #
# Tests.
# --------------------------------------------------------------------------- #


def test_coverage_passes_on_real_corpus(cloned_corpus: Path) -> None:
    """Untouched clone of the real corpus → coverage block prints PASS, exit 0."""
    proc = _run_validator(cloned_corpus)

    assert proc.returncode == 0, f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
    assert "Coverage check passed" in proc.stdout, proc.stdout
    assert "Coverage check failed" not in proc.stdout, proc.stdout
    # Sanity: the full 150-scenario corpus validated.
    assert "Validated 150 files: 150 passed, 0 failed" in proc.stdout, proc.stdout


def test_coverage_fails_when_doc_type_missing(cloned_corpus: Path) -> None:
    """Delete every air-ticket scenario → coverage block names ``air_ticket``."""
    scenarios_root = cloned_corpus / "corpus" / "pdf" / "layer1" / "scenarios"
    deleted = 0
    for scenario_dir in scenarios_root.iterdir():
        # The generator's slug convention starts the air-ticket scenarios
        # with ``-air-`` immediately after the ``NNN`` prefix.
        if "-air-" in scenario_dir.name:
            scenario_dir.unlink()  # symlinks → unlink, not rmtree
            deleted += 1
    # Sanity: we removed the 24 base + 6 multi-leg air scenarios.
    assert deleted == 30, f"expected 30 air scenarios on disk, found {deleted}"

    proc = _run_validator(cloned_corpus)

    assert proc.returncode == 1, f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
    assert "Coverage check failed" in proc.stdout, proc.stdout
    assert "coverage: missing document_type 'air_ticket'" in proc.stdout, proc.stdout


def test_coverage_fails_when_multileg_missing(cloned_corpus: Path) -> None:
    """Delete the multi-leg scenarios (145–150) → coverage block names multi-leg."""
    scenarios_root = cloned_corpus / "corpus" / "pdf" / "layer1" / "scenarios"
    deleted = 0
    for scenario_dir in scenarios_root.iterdir():
        # The 6 multi-leg air scenarios live under ``-air-multileg-`` slugs.
        if "-air-multileg-" in scenario_dir.name:
            scenario_dir.unlink()
            deleted += 1
    assert deleted == 6, f"expected 6 multi-leg scenarios on disk, found {deleted}"

    proc = _run_validator(cloned_corpus)

    assert proc.returncode == 1, f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
    assert "Coverage check failed" in proc.stdout, proc.stdout
    # Multi-leg-specific failure line; the count after deletion is 0 (the
    # remaining 144 base scenarios all have ``cities[]`` of length 1 or 2).
    assert (
        "coverage: only 0 multi-leg scenarios (cities[] ≥ 3); expected ≥3"
        in proc.stdout
    ), proc.stdout
