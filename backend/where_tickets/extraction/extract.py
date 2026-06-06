"""PDF extractor entry point — stub for Slice 1 of spec 006.

This module owns the public surface the corpus runner (and, later, the SQS
pipeline) calls into: :func:`extract_pdf`, :class:`ExtractedFields`, and
:class:`ExtractionFailedError`. The TypedDict shape mirrors the runner's
``ExtractedFields`` in ``corpus/pdf/runner.py`` exactly — the runner is a
script, not an importable package, so the contract is duplicated here rather
than imported.

Slice 1 only wires the symbol so downstream code can import it; the real
text + vision extraction paths land in Slice 5 onwards. Until then,
:func:`extract_pdf` always raises :class:`ExtractionFailedError` — the runner
treats that as a per-file failure, which is the correct behaviour for an
unwired extractor.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, NotRequired, TypedDict

__all__ = [
    "AccommodationEntry",
    "ExtractedFields",
    "ExtractionFailedError",
    "PriceEntry",
    "StationEntry",
    "VenueEntry",
    "extract_pdf",
]


class ExtractionFailedError(Exception):
    """Raised when every extraction path (text, vision, …) has failed.

    Callers should treat this as "the PDF couldn't be read" and surface the
    document as unread to the user, rather than dropping it silently.
    """


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


def extract_pdf(pdf_path: Path) -> ExtractedFields:
    """Extract structured fields from ``pdf_path``.

    Slice 1 stub: always raises :class:`ExtractionFailedError`. Slice 5 wires
    the real text path; Slice 6+ adds the vision fallback. The signature and
    return type are stable from here on.
    """
    del pdf_path  # unused in the stub; signature is the contract under test
    raise ExtractionFailedError("not implemented yet")
