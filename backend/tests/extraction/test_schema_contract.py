"""Schema-contract tests for the extraction package.

Two things are gated here:

1. :func:`where_tickets.extraction.schema.validate` accepts a well-formed
   payload, flags a missing required field, and flags a wrong
   ``document_type`` enum.
2. :data:`where_tickets.extraction.schema.EXTRACTOR_SCHEMA` (and therefore the
   tool ``input_schema`` derived from it in
   :mod:`where_tickets.extraction.prompts`) does NOT require the corpus-only
   metadata fields ``scenario_id`` / ``noise_seed``.

The whole module is gated on ``jsonschema`` so ``just test`` (no extraction
group) collects-but-skips, matching the pattern used by ``test_pdf_helpers``
for PyMuPDF.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

# `jsonschema` ships only with the optional `extraction` dep group; without it,
# every test in this file is skipped (same pattern as test_pdf_helpers.py).
pytest.importorskip("jsonschema")

from where_tickets.extraction.prompts import (  # noqa: E402 — importorskip gate
    TOOL_EMIT_EXTRACTED_FIELDS,
)
from where_tickets.extraction.schema import (  # noqa: E402 — importorskip gate
    EXTRACTOR_SCHEMA,
    validate,
)


def _valid_air_ticket_payload() -> dict[str, Any]:
    """A hand-built, minimal but schema-complete air-ticket payload.

    One origin city, two stations (departure + arrival), one traveler, no
    accommodations / venues / prices / QR codes. Datetimes use the
    ``isoLocalDatetime`` shape required by the schema.
    """
    return {
        "document_type": "air_ticket",
        "cities": ["Paris", "Lisbon"],
        "stations": [
            {
                "city": "Paris",
                "kind": "airport",
                "identifier": "CDG",
                "departure_datetime": "2027-03-01T08:30:00",
            },
            {
                "city": "Lisbon",
                "kind": "airport",
                "identifier": "LIS",
                "arrival_datetime": "2027-03-01T10:45:00",
            },
        ],
        "accommodations": [],
        "venues": [],
        "travelers": ["Jane Doe"],
        "prices": [],
        "qr_codes": [],
        "pdf_kind": "text",
    }


def test_valid_payload_validates_clean() -> None:
    """A hand-built payload that satisfies the schema validates cleanly."""
    ok, errors = validate(_valid_air_ticket_payload())

    assert ok is True
    assert errors == []


def test_missing_required_field_fails() -> None:
    """Removing a required top-level field (``cities``) fails validation."""
    payload = _valid_air_ticket_payload()
    del payload["cities"]

    ok, errors = validate(payload)

    assert ok is False
    assert errors, "expected at least one validation error"
    assert any("cities" in msg for msg in errors), (
        f"expected an error mentioning 'cities', got: {errors!r}"
    )


def test_wrong_document_type_enum_fails() -> None:
    """A ``document_type`` outside the enum fails validation."""
    payload = _valid_air_ticket_payload()
    payload["document_type"] = "movie_ticket"  # not in the enum

    ok, errors = validate(payload)

    assert ok is False
    assert errors, "expected at least one validation error"
    assert any("document_type" in msg for msg in errors), (
        f"expected an error mentioning 'document_type', got: {errors!r}"
    )


def test_extractor_schema_does_not_require_scenario_id() -> None:
    """Corpus-only metadata fields must NOT be required of the extractor."""
    required = EXTRACTOR_SCHEMA.get("required", [])

    assert "scenario_id" not in required, (
        "scenario_id is corpus-only metadata; the extractor never emits it"
    )
    assert "noise_seed" not in required, (
        "noise_seed is corpus-only metadata; the extractor never emits it"
    )
    # Sanity: the rest of the corpus-required keys ARE still required.
    for required_key in (
        "document_type",
        "cities",
        "stations",
        "accommodations",
        "venues",
        "travelers",
        "prices",
        "qr_codes",
        "pdf_kind",
    ):
        assert required_key in required, (
            f"corpus-required key {required_key!r} dropped from EXTRACTOR_SCHEMA"
        )


def test_tool_input_schema_matches_extractor_schema() -> None:
    """The tool's ``input_schema`` IS the shared :data:`EXTRACTOR_SCHEMA`.

    Equality (not just structural equivalence) holds because the prompts
    module passes the same object through — there is exactly one source of
    truth. A `copy.deepcopy` would also pass; we assert the stronger
    invariant to catch any future "wrap and mutate" change.
    """
    tool_schema = TOOL_EMIT_EXTRACTED_FIELDS["input_schema"]

    # `cache_control` lives on the outer tool wrapper, NOT inside input_schema.
    assert "cache_control" not in tool_schema, (
        "cache_control must live on the tool wrapper, not inside input_schema"
    )
    assert tool_schema == EXTRACTOR_SCHEMA

    # Defensive: mutating EXTRACTOR_SCHEMA via the tool object would silently
    # corrupt validator state. A deepcopy comparison is the simplest guard.
    assert tool_schema == copy.deepcopy(EXTRACTOR_SCHEMA)
