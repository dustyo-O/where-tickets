"""Offline tests for :mod:`spikes.integration.runner`.

Stub-extractor coverage: every test injects an in-process callable so the
production extractor (``where_tickets.extraction.extract_pdf``) is never
imported. This keeps the persistent backend venv free of ``anthropic`` —
the runner's ``--extractor-import-path`` mechanism is exercised end to end
via the in-process stub, mirroring how :mod:`corpus.pdf.runner`'s tests stub
the same seam.

Covered cases (mirrors Slice 7 sub-task 5):

- Discovery: tempdir integration root produces :class:`Trip` records; a
  missing ``manifest.json`` or ``expected-route.json`` surfaces as a
  discovery error (the trip is reported FAILed, not silently skipped).
- Single-trip happy path: stub extractor returns canned :class:`ExtractedFields`
  payloads; the runner produces a :class:`WorkingRoute` that matches the
  expected-route and the trip PASSes.
- Adapter error: stub returns a transit payload with only 1 station, which
  the adapter rejects; the trip FAILs with adapter-error reason and the
  runner exit code is 1.
- ``ExtractionFailedError`` without ``expect_unreadable``: the trip FAILs.
- ``ExtractionFailedError`` with ``expect_unreadable=True``: the trip
  continues using only the readable docs and PASSes when the remaining
  docs reproduce the expected route.
- ``--trip <slug>`` filter: only the matching trip runs.
- ``--no-route-check`` flag: trip PASSes when extraction + adapter + engine
  all succeed, even if the actual route would have failed scoring.
- ``--json-report``: writes a file matching :func:`build_report_dict`'s shape.

The production extractor module is NEVER imported at module top.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from spikes.integration.adapter import ExtractedFields
from spikes.integration.report import TripResult, build_report_dict
from spikes.integration.runner import (
    Manifest,
    discover,
    main,
    run_trip,
    run_trips,
)


# --------------------------------------------------------------------------- #
# Stub-extractor scaffolding
# --------------------------------------------------------------------------- #


class _StubExtractionFailed(Exception):
    """In-process stand-in for ``ExtractionFailedError`` — tests raise this."""


def _air_return_paris_lisbon_payload() -> ExtractedFields:
    """The canned payload for ``003-air-return-1pax-paris-lisbon/document.pdf``.

    Mirrors the layer-1 fixture exactly; the runner uses this to round-trip
    through adapter + engine + scoring without touching Bedrock.
    """
    return {
        "document_type": "air_ticket",
        "cities": ["Paris", "Lisbon"],
        "stations": [
            {
                "city": "Paris",
                "kind": "airport",
                "identifier": "CDG",
                "departure_datetime": "2027-03-11T08:30:00",
            },
            {
                "city": "Lisbon",
                "kind": "airport",
                "identifier": "LIS",
                "arrival_datetime": "2027-03-11T10:45:00",
                "departure_datetime": "2027-03-15T18:15:00",
            },
            {
                "city": "Paris",
                "kind": "airport",
                "identifier": "CDG",
                "arrival_datetime": "2027-03-15T20:30:00",
            },
        ],
        "accommodations": [],
        "venues": [],
        "travelers": ["Ines Marques"],
        "prices": [{"amount": 111.79, "currency": "EUR"}],
        "qr_codes": [],
        "pdf_kind": "rasterized",
    }


def _air_paris_lisbon_oneway_payload() -> ExtractedFields:
    """A one-way outbound payload (Paris -> Lisbon) for chaining scenarios."""
    return {
        "document_type": "air_ticket",
        "cities": ["Paris", "Lisbon"],
        "stations": [
            {
                "city": "Paris",
                "kind": "airport",
                "identifier": "CDG",
                "departure_datetime": "2027-03-11T08:30:00",
            },
            {
                "city": "Lisbon",
                "kind": "airport",
                "identifier": "LIS",
                "arrival_datetime": "2027-03-11T10:45:00",
            },
        ],
        "accommodations": [],
        "venues": [],
        "travelers": ["Ines Marques"],
        "prices": [],
        "qr_codes": [],
        "pdf_kind": "text",
    }


def _air_lisbon_paris_return_payload() -> ExtractedFields:
    """A one-way return payload (Lisbon -> Paris) for chaining scenarios."""
    return {
        "document_type": "air_ticket",
        "cities": ["Lisbon", "Paris"],
        "stations": [
            {
                "city": "Lisbon",
                "kind": "airport",
                "identifier": "LIS",
                "departure_datetime": "2027-03-15T18:15:00",
            },
            {
                "city": "Paris",
                "kind": "airport",
                "identifier": "CDG",
                "arrival_datetime": "2027-03-15T20:30:00",
            },
        ],
        "accommodations": [],
        "venues": [],
        "travelers": ["Ines Marques"],
        "prices": [],
        "qr_codes": [],
        "pdf_kind": "text",
    }


def _build_extractor_stub(
    payloads: dict[str, ExtractedFields],
    *,
    raises: dict[str, BaseException] | None = None,
) -> Callable[[Path], ExtractedFields]:
    """Build an extractor stub that dispatches on the PDF basename.

    Keys are the basename of the PDF path (e.g. ``"document.pdf"``); the stub
    looks the incoming :class:`Path` up by basename. Missing keys raise so
    test failures surface as a clear KeyError rather than a silent default.
    """
    raises = raises or {}

    def _extractor(pdf_path: Path) -> ExtractedFields:
        key = pdf_path.name
        if key in raises:
            raise raises[key]
        # The runner appends the manifest's PDF path to layer1_root, so the
        # incoming path's parent's name is the scenario slug — use the full
        # relative path as the alternative lookup key.
        rel = pdf_path.parent.name + "/" + pdf_path.name
        if rel in raises:
            raise raises[rel]
        if rel in payloads:
            return payloads[rel]
        if key in payloads:
            return payloads[key]
        msg = f"stub extractor has no payload for {pdf_path!r}"
        raise KeyError(msg)

    return _extractor


# --------------------------------------------------------------------------- #
# Tempdir scaffolding
# --------------------------------------------------------------------------- #


def _write_trip(
    integration_root: Path,
    slug: str,
    *,
    manifest: dict[str, Any],
    expected_route: dict[str, Any],
) -> Path:
    trip_dir = integration_root / slug
    trip_dir.mkdir(parents=True, exist_ok=True)
    (trip_dir / "manifest.json").write_text(json.dumps(manifest))
    (trip_dir / "expected-route.json").write_text(json.dumps(expected_route))
    return trip_dir


def _return_paris_lisbon_expected_route() -> dict[str, Any]:
    src = "003-air-return-1pax-paris-lisbon/document.pdf"
    return {
        "travelers": ["Ines Marques"],
        "stops": [
            {
                "city": "Paris",
                "departureAt": "2027-03-11T08:30:00Z",
                "travelers": ["Ines Marques"],
            },
            {
                "city": "Lisbon",
                "arrivalAt": "2027-03-11T10:45:00Z",
                "departureAt": "2027-03-15T18:15:00Z",
                "travelers": ["Ines Marques"],
            },
            {
                "city": "Paris",
                "arrivalAt": "2027-03-15T20:30:00Z",
                "travelers": ["Ines Marques"],
            },
        ],
        "transits": [
            {
                "from": "Paris",
                "to": "Lisbon",
                "mode": "air",
                "departureAt": "2027-03-11T08:30:00Z",
                "arrivalAt": "2027-03-11T10:45:00Z",
                "travelers": ["Ines Marques"],
                "sourceFragmentId": src,
            },
            {
                "from": "Lisbon",
                "to": "Paris",
                "mode": "air",
                "departureAt": "2027-03-15T18:15:00Z",
                "arrivalAt": "2027-03-15T20:30:00Z",
                "travelers": ["Ines Marques"],
                "sourceFragmentId": src,
            },
        ],
    }


@pytest.fixture()
def integration_root(tmp_path: Path) -> Path:
    return tmp_path / "integration"


@pytest.fixture()
def layer1_root(tmp_path: Path) -> Path:
    """Standard layer1 fake — only directory structure matters for the stub.

    The stub extractor dispatches on the PDF basename, so the files don't
    need to exist. Tests that need a "real" file presence touch it
    themselves.
    """
    return tmp_path / "layer1"


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


def test_discover_returns_trip_records_for_well_formed_directories(
    integration_root: Path,
) -> None:
    _write_trip(
        integration_root,
        "01-air-return-1pax-paris-lisbon",
        manifest={
            "travelers": ["Ines Marques"],
            "documents": [
                {"pdf": "003-air-return-1pax-paris-lisbon/document.pdf"}
            ],
        },
        expected_route=_return_paris_lisbon_expected_route(),
    )
    _write_trip(
        integration_root,
        "02-air-oneway-1pax",
        manifest={
            "travelers": ["Ines Marques"],
            "documents": [
                {"pdf": "001-air-1leg-1pax-paris-lisbon/document.pdf"}
            ],
        },
        expected_route=_return_paris_lisbon_expected_route(),
    )

    trips, errors = discover(integration_root)
    assert errors == []
    slugs = [t.slug for t in trips]
    assert slugs == ["01-air-return-1pax-paris-lisbon", "02-air-oneway-1pax"]
    assert all(isinstance(t.manifest, Manifest) for t in trips)


def test_discover_missing_manifest_emits_discovery_error(
    integration_root: Path,
) -> None:
    trip_dir = integration_root / "broken-trip"
    trip_dir.mkdir(parents=True)
    (trip_dir / "expected-route.json").write_text(
        json.dumps(_return_paris_lisbon_expected_route())
    )

    trips, errors = discover(integration_root)
    assert trips == []
    assert len(errors) == 1
    assert errors[0].slug == "broken-trip"
    assert "missing manifest.json" in errors[0].reason


def test_discover_missing_expected_route_emits_discovery_error(
    integration_root: Path,
) -> None:
    trip_dir = integration_root / "no-route"
    trip_dir.mkdir(parents=True)
    (trip_dir / "manifest.json").write_text(
        json.dumps(
            {
                "travelers": ["Ines Marques"],
                "documents": [{"pdf": "any.pdf"}],
            }
        )
    )

    trips, errors = discover(integration_root)
    assert trips == []
    assert len(errors) == 1
    assert errors[0].slug == "no-route"
    assert "missing expected-route.json" in errors[0].reason


# --------------------------------------------------------------------------- #
# run_trip — single happy path
# --------------------------------------------------------------------------- #


def test_run_trip_happy_path_paris_lisbon_return_pass(
    integration_root: Path,
    layer1_root: Path,
) -> None:
    _write_trip(
        integration_root,
        "01-air-return-1pax-paris-lisbon",
        manifest={
            "travelers": ["Ines Marques"],
            "documents": [
                {"pdf": "003-air-return-1pax-paris-lisbon/document.pdf"}
            ],
        },
        expected_route=_return_paris_lisbon_expected_route(),
    )
    trips, _ = discover(integration_root)
    extractor = _build_extractor_stub(
        {
            "003-air-return-1pax-paris-lisbon/document.pdf": (
                _air_return_paris_lisbon_payload()
            )
        }
    )

    result = run_trip(
        trips[0],
        extractor=extractor,
        pdf_root=layer1_root,
        extraction_failed_error=_StubExtractionFailed,
    )

    assert result.passed is True
    assert result.failures == []
    assert result.scoring_reason is None
    assert len(result.documents) == 1
    doc = result.documents[0]
    assert doc.extracted is True
    assert doc.adapted is True
    assert doc.folded is True
    assert doc.error is None
    assert doc.pdf_kind == "rasterized"


# --------------------------------------------------------------------------- #
# run_trip — adapter error
# --------------------------------------------------------------------------- #


def test_run_trip_adapter_error_marks_trip_failed(
    integration_root: Path,
    layer1_root: Path,
) -> None:
    _write_trip(
        integration_root,
        "01-bad-adapter",
        manifest={
            "travelers": ["Ines Marques"],
            "documents": [{"pdf": "fake/document.pdf"}],
        },
        expected_route=_return_paris_lisbon_expected_route(),
    )
    trips, _ = discover(integration_root)

    # Air ticket with only 1 station — adapter requires >= 2.
    bad_payload: ExtractedFields = {
        "document_type": "air_ticket",
        "cities": ["Paris"],
        "stations": [
            {
                "city": "Paris",
                "kind": "airport",
                "identifier": "CDG",
                "departure_datetime": "2027-03-11T08:30:00",
            }
        ],
        "accommodations": [],
        "venues": [],
        "travelers": ["Ines Marques"],
        "prices": [],
        "qr_codes": [],
        "pdf_kind": "text",
    }
    extractor = _build_extractor_stub(
        {"fake/document.pdf": bad_payload}
    )

    result = run_trip(
        trips[0],
        extractor=extractor,
        pdf_root=layer1_root,
        extraction_failed_error=_StubExtractionFailed,
    )

    assert result.passed is False
    assert any("adapter error" in f for f in result.failures)
    assert result.documents[0].adapted is False
    assert result.documents[0].extracted is True


# --------------------------------------------------------------------------- #
# run_trip — extraction failures
# --------------------------------------------------------------------------- #


def test_run_trip_extraction_failure_without_expect_unreadable_fails(
    integration_root: Path,
    layer1_root: Path,
) -> None:
    _write_trip(
        integration_root,
        "01-extract-fails",
        manifest={
            "travelers": ["Ines Marques"],
            "documents": [{"pdf": "broken/document.pdf"}],
        },
        expected_route=_return_paris_lisbon_expected_route(),
    )
    trips, _ = discover(integration_root)
    extractor = _build_extractor_stub(
        {},
        raises={"broken/document.pdf": _StubExtractionFailed("boom")},
    )

    result = run_trip(
        trips[0],
        extractor=extractor,
        pdf_root=layer1_root,
        extraction_failed_error=_StubExtractionFailed,
    )

    assert result.passed is False
    assert any(
        "extraction failed" in f and "not flagged expect_unreadable" in f
        for f in result.failures
    )
    assert result.documents[0].extracted is False


def test_run_trip_adapter_failure_with_expect_unreadable_continues(
    integration_root: Path,
    layer1_root: Path,
) -> None:
    """DUS-31 Slice 8: adapter errors on ``expect_unreadable`` docs don't fail the trip.

    Some PDFs (blank, malformed, very low text density) make it through the
    extractor without raising :class:`ExtractionFailedError` — the extractor
    returns a payload that the adapter then rejects because the structured
    place arrays don't meet the per-document-type minimum arity. When the
    manifest entry flags ``expect_unreadable: true``, the runner treats this
    case the same as an extraction failure: the document is skipped, the
    trip is built from the rest.
    """
    _write_trip(
        integration_root,
        "01-unreadable-by-adapter",
        manifest={
            "travelers": ["Ines Marques"],
            "documents": [
                {"pdf": "blank/document.pdf", "expect_unreadable": True},
                {"pdf": "003-air-return-1pax-paris-lisbon/document.pdf"},
            ],
        },
        expected_route=_return_paris_lisbon_expected_route(),
    )
    trips, _ = discover(integration_root)
    # Air ticket payload with NO stations — adapter rejects.
    empty_payload: ExtractedFields = {
        "document_type": "air_ticket",
        "cities": [],
        "stations": [],
        "accommodations": [],
        "venues": [],
        "travelers": ["Ines Marques"],
        "prices": [],
        "qr_codes": [],
        "pdf_kind": "text",
    }
    extractor = _build_extractor_stub(
        {
            "blank/document.pdf": empty_payload,
            "003-air-return-1pax-paris-lisbon/document.pdf": (
                _air_return_paris_lisbon_payload()
            ),
        }
    )

    result = run_trip(
        trips[0],
        extractor=extractor,
        pdf_root=layer1_root,
        extraction_failed_error=_StubExtractionFailed,
    )

    assert result.passed is True
    assert result.failures == []
    assert result.documents[0].error is not None
    assert "expected" in result.documents[0].error
    assert result.documents[1].folded is True


def test_run_trip_extraction_failure_with_expect_unreadable_continues(
    integration_root: Path,
    layer1_root: Path,
) -> None:
    # Two-doc trip: first PDF is flagged expect_unreadable; second reproduces
    # the expected route on its own. Trip should PASS.
    _write_trip(
        integration_root,
        "01-unreadable-then-good",
        manifest={
            "travelers": ["Ines Marques"],
            "documents": [
                {"pdf": "unreadable/document.pdf", "expect_unreadable": True},
                {"pdf": "003-air-return-1pax-paris-lisbon/document.pdf"},
            ],
        },
        expected_route=_return_paris_lisbon_expected_route(),
    )
    trips, _ = discover(integration_root)
    extractor = _build_extractor_stub(
        {
            "003-air-return-1pax-paris-lisbon/document.pdf": (
                _air_return_paris_lisbon_payload()
            )
        },
        raises={
            "unreadable/document.pdf": _StubExtractionFailed("scan unreadable")
        },
    )

    result = run_trip(
        trips[0],
        extractor=extractor,
        pdf_root=layer1_root,
        extraction_failed_error=_StubExtractionFailed,
    )

    assert result.passed is True
    assert result.failures == []
    assert result.documents[0].error is not None
    assert "expected" in result.documents[0].error
    assert result.documents[1].folded is True


# --------------------------------------------------------------------------- #
# CLI: --trip filter
# --------------------------------------------------------------------------- #


def test_cli_trip_filter_runs_only_matching_trip(
    integration_root: Path,
    layer1_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    _write_trip(
        integration_root,
        "01-good",
        manifest={
            "travelers": ["Ines Marques"],
            "documents": [
                {"pdf": "003-air-return-1pax-paris-lisbon/document.pdf"}
            ],
        },
        expected_route=_return_paris_lisbon_expected_route(),
    )
    _write_trip(
        integration_root,
        "02-other",
        manifest={
            "travelers": ["Ines Marques"],
            "documents": [{"pdf": "fake.pdf"}],
        },
        expected_route=_return_paris_lisbon_expected_route(),
    )
    extractor = _build_extractor_stub(
        {
            "003-air-return-1pax-paris-lisbon/document.pdf": (
                _air_return_paris_lisbon_payload()
            )
        }
    )

    # Wire the runner to our stub by patching _load_extractor; this exercises
    # the CLI seam without needing a real importable extractor module.
    import spikes.integration.runner as runner_module  # noqa: PLC0415

    monkeypatch.setattr(
        runner_module, "_load_extractor", lambda _dotted: extractor
    )
    monkeypatch.setattr(
        runner_module,
        "_load_extraction_failed_error",
        lambda: _StubExtractionFailed,
    )

    report_path = tmp_path / "report.json"
    exit_code = main(
        [
            "--integration-root",
            str(integration_root),
            "--layer1-root",
            str(layer1_root),
            "--trip",
            "01-good",
            "--json-report",
            str(report_path),
        ]
    )

    assert exit_code == 0
    captured = capsys.readouterr().out
    assert "01-good" in captured
    assert "02-other" not in captured

    # JSON report shape
    payload = json.loads(report_path.read_text())
    assert payload["version"] == 1
    assert payload["summary"]["total"] == 1
    assert payload["summary"]["passed"] == 1
    assert payload["summary"]["failed"] == 0
    assert {t["slug"] for t in payload["trips"]} == {"01-good"}


# --------------------------------------------------------------------------- #
# CLI: --no-route-check
# --------------------------------------------------------------------------- #


def test_cli_no_route_check_passes_when_pipeline_succeeds(
    integration_root: Path,
    layer1_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The expected-route in this trip is deliberately wrong — but
    # --no-route-check skips the scoring step, so the trip should still
    # PASS as long as extraction + adapter + engine all succeed.
    wrong_expected = _return_paris_lisbon_expected_route()
    wrong_expected["stops"][0]["city"] = "Madrid"  # deliberately wrong
    _write_trip(
        integration_root,
        "01-skip-scoring",
        manifest={
            "travelers": ["Ines Marques"],
            "documents": [
                {"pdf": "003-air-return-1pax-paris-lisbon/document.pdf"}
            ],
        },
        expected_route=wrong_expected,
    )
    extractor = _build_extractor_stub(
        {
            "003-air-return-1pax-paris-lisbon/document.pdf": (
                _air_return_paris_lisbon_payload()
            )
        }
    )

    import spikes.integration.runner as runner_module  # noqa: PLC0415

    monkeypatch.setattr(
        runner_module, "_load_extractor", lambda _dotted: extractor
    )
    monkeypatch.setattr(
        runner_module,
        "_load_extraction_failed_error",
        lambda: _StubExtractionFailed,
    )

    exit_code = main(
        [
            "--integration-root",
            str(integration_root),
            "--layer1-root",
            str(layer1_root),
            "--no-route-check",
        ]
    )
    captured = capsys.readouterr().out
    assert exit_code == 0
    assert "PASS" in captured

    # Same trip WITHOUT --no-route-check should fail (sanity check).
    exit_code_strict = main(
        [
            "--integration-root",
            str(integration_root),
            "--layer1-root",
            str(layer1_root),
        ]
    )
    assert exit_code_strict == 1


# --------------------------------------------------------------------------- #
# build_report_dict shape
# --------------------------------------------------------------------------- #


def test_build_report_dict_shape() -> None:
    sample = TripResult(slug="x-test", passed=True)
    payload = build_report_dict([sample])
    assert payload["version"] == 1
    assert payload["summary"] == {"total": 1, "passed": 1, "failed": 0}
    trips = payload["trips"]
    assert isinstance(trips, list)
    assert trips[0]["slug"] == "x-test"
    assert trips[0]["passed"] is True
    assert "failures" in trips[0]
    assert "documents" in trips[0]


# --------------------------------------------------------------------------- #
# run_trips loops over multiple trips
# --------------------------------------------------------------------------- #


def test_run_trips_returns_one_result_per_trip(
    integration_root: Path,
    layer1_root: Path,
) -> None:
    _write_trip(
        integration_root,
        "01-good",
        manifest={
            "travelers": ["Ines Marques"],
            "documents": [
                {"pdf": "003-air-return-1pax-paris-lisbon/document.pdf"}
            ],
        },
        expected_route=_return_paris_lisbon_expected_route(),
    )
    _write_trip(
        integration_root,
        "02-bad-route",
        manifest={
            "travelers": ["Ines Marques"],
            "documents": [
                {"pdf": "001-oneway/document.pdf"},
            ],
        },
        expected_route=_return_paris_lisbon_expected_route(),
    )
    trips, _ = discover(integration_root)
    extractor = _build_extractor_stub(
        {
            "003-air-return-1pax-paris-lisbon/document.pdf": (
                _air_return_paris_lisbon_payload()
            ),
            "001-oneway/document.pdf": _air_paris_lisbon_oneway_payload(),
        }
    )

    results = run_trips(
        trips,
        extractor=extractor,
        pdf_root=layer1_root,
        extraction_failed_error=_StubExtractionFailed,
    )

    assert [r.slug for r in results] == ["01-good", "02-bad-route"]
    assert results[0].passed is True
    # The second trip's manifest gives the engine a one-way Paris->Lisbon,
    # which doesn't match the return expected-route fixture.
    assert results[1].passed is False
    assert results[1].scoring_reason is not None
