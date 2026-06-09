"""Pure-mapping adapter from the extractor's :class:`ExtractedFields` to the
engine's :data:`Fragment` discriminated union.

DUS-31 Slice 6. No I/O, no Bedrock, no PDF parsing — just data mapping. The
integration runner (Slice 7) will call this once per PDF after the live
extractor returns, then feed the resulting fragment into
:func:`spikes.route_engine_algorithmic.engine.update_route`.

The :class:`ExtractedFields` TypedDict (and its nested
:class:`StationEntry` / :class:`AccommodationEntry` / :class:`VenueEntry` /
:class:`PriceEntry`) is duplicated here rather than imported from
``where_tickets.extraction.extract`` because the production extractor module
pulls in ``anthropic`` at import time, and importing it would force the
integration spike's persistent backend venv to carry that dep. The corpus
runner (``corpus/pdf/runner.py``) already follows this convention; the contract
is fixed by the JSON schema at ``corpus/pdf/schema/expected-fields.schema.json``
so the two TypedDict copies stay structurally aligned.

Mapping highlights:

- ``document_type`` snake_case → kebab-case (e.g. ``"air_ticket"`` →
  ``"air-ticket"``).
- Datetimes: ISO-local (``"2027-03-11T08:30:00"``) → tz-aware UTC
  (``datetime(2027, 3, 11, 8, 30, 0, tzinfo=UTC)``). Treat-printed-as-UTC is
  the convention the engine corpus already uses; deferring real timezone
  handling stays Slice-7-or-later.
- ``pnr`` / ``confirmation_code``: the extractor doesn't emit either. Both
  fall back to ``source_document_id`` as a deterministic placeholder.
- Minimum-arity guards mirror the Pydantic models:

  * transit requires ``len(stations) >= 2``;
  * accommodation requires ``len(accommodations) >= 1``;
  * supplementary requires ``len(travelers) >= 1``.

  Violations raise :class:`AdapterError` with a clear message so the integration
  runner surfaces the failure at the adapter boundary rather than via an
  opaque Pydantic ``ValidationError`` deep inside fragment construction.

``pdf_kind`` and ``extraction_path`` are NOT propagated to the engine fragment
(the engine doesn't carry those fields). The runner can read them straight off
the :class:`ExtractedFields` payload for the JSON report.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, NotRequired, TypedDict

from spikes.route_engine_llm.models import (
    AccommodationFragment,
    Fragment,
    FragmentAccommodation,
    FragmentVenue,
    Price,
    Station,
    SupplementaryFragment,
    TransitTicketFragment,
)

__all__ = [
    "AccommodationEntry",
    "AdapterError",
    "ExtractedFields",
    "PriceEntry",
    "StationEntry",
    "VenueEntry",
    "extracted_fields_to_fragment",
]


# --------------------------------------------------------------------------- #
# ExtractedFields TypedDict (mirrors corpus/pdf/runner.py exactly).
#
# Duplicated rather than imported because importing
# ``where_tickets.extraction.extract`` would force the spike venv to carry
# ``anthropic``. The JSON schema at
# ``corpus/pdf/schema/expected-fields.schema.json`` is the single source of
# truth; the runner and this adapter both shadow it as TypedDicts so callers
# get static-typing help.
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Errors + dispatch table
# --------------------------------------------------------------------------- #


class AdapterError(Exception):
    """Raised when an :class:`ExtractedFields` payload cannot be mapped.

    Used for arity guards (transit < 2 stations, accommodation < 1 entry,
    supplementary < 1 traveler) and any unknown ``document_type``. Lets the
    integration runner surface mapping failures at the adapter boundary
    rather than via an opaque Pydantic ``ValidationError``.
    """


# snake_case (extractor) → kebab-case (engine fragment) for `documentType`.
# Kept as a single module-level dict so the six-way mapping is obvious at a
# glance and trivially extended.
_DOC_TYPE_MAP: dict[str, str] = {
    "air_ticket": "air-ticket",
    "rail_ticket": "rail-ticket",
    "bus_ticket": "bus-ticket",
    "hotel_booking": "hotel-booking",
    "airbnb_booking": "airbnb-booking",
    "supplementary": "supplementary",
}

_TRANSIT_TYPES: frozenset[str] = frozenset({"air_ticket", "rail_ticket", "bus_ticket"})
_ACCOMMODATION_TYPES: frozenset[str] = frozenset({"hotel_booking", "airbnb_booking"})


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _parse_to_utc(value: str) -> datetime:
    """Parse an ISO-local datetime and stamp it with UTC.

    The extractor's datetimes are ISO local with no timezone designator (see
    ``corpus/pdf/schema/expected-fields.schema.json $defs.isoLocalDatetime``);
    the engine's existing corpus / models use tz-aware UTC datetimes. This
    helper bridges the two via the "treat printed time as UTC" convention,
    which is the simplest mapping that keeps the engine's existing tz-aware
    comparisons working.
    """
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


# The fragment Pydantic models do NOT all enable ``populate_by_name``
# (``TransitTicketFragment`` and ``AccommodationFragment`` both use
# strict alias-only construction). Building each nested model via
# ``model_validate({...})`` with alias keys keeps the call sites uniform
# regardless of per-model config, and avoids pyright tripping on
# ``__init__`` kwargs the alias-only models don't accept by snake_case.


def _station_from_entry(entry: StationEntry) -> Station:
    """Map one extractor StationEntry to an engine :class:`Station`."""
    payload: dict[str, object] = {
        "city": entry["city"],
        "kind": entry["kind"],
        "identifier": entry["identifier"],
    }
    dep = entry.get("departure_datetime")
    if dep is not None:
        payload["departureAt"] = _parse_to_utc(dep)
    arr = entry.get("arrival_datetime")
    if arr is not None:
        payload["arrivalAt"] = _parse_to_utc(arr)
    return Station.model_validate(payload)


def _accommodation_from_entry(entry: AccommodationEntry) -> FragmentAccommodation:
    """Map one extractor AccommodationEntry to a :class:`FragmentAccommodation`."""
    return FragmentAccommodation.model_validate(
        {
            "city": entry["city"],
            "kind": entry["kind"],
            "identifier": entry["identifier"],
            "checkInAt": _parse_to_utc(entry["check_in_datetime"]),
            "checkOutAt": _parse_to_utc(entry["check_out_datetime"]),
        }
    )


def _venue_from_entry(entry: VenueEntry) -> FragmentVenue:
    """Map one extractor VenueEntry to a :class:`FragmentVenue`."""
    payload: dict[str, object] = {
        "city": entry["city"],
        "kind": entry["kind"],
        "identifier": entry["identifier"],
    }
    valid_from = entry.get("valid_from_datetime")
    if valid_from is not None:
        payload["validFromAt"] = _parse_to_utc(valid_from)
    valid_to = entry.get("valid_to_datetime")
    if valid_to is not None:
        payload["validToAt"] = _parse_to_utc(valid_to)
    return FragmentVenue.model_validate(payload)


def _price_from_entry(entry: PriceEntry) -> Price:
    """Map one extractor PriceEntry to an engine :class:`Price`."""
    return Price.model_validate(
        {"amount": entry["amount"], "currency": entry["currency"]}
    )


# --------------------------------------------------------------------------- #
# Per-variant fragment builders
# --------------------------------------------------------------------------- #


def _build_transit_fragment(
    fields: ExtractedFields,
    *,
    source_document_id: str,
) -> TransitTicketFragment:
    """Build a :class:`TransitTicketFragment` from the extractor payload.

    The extractor doesn't emit a PNR, so ``pnr`` falls back to
    ``source_document_id`` as a deterministic placeholder. The engine doesn't
    route on the PNR value — it's carried only for traceability — and using
    the source document id keeps the placeholder distinct per fragment.
    """
    stations = fields["stations"]
    if len(stations) < 2:
        msg = (
            f"transit ticket {source_document_id!r} needs >=2 stations, "
            f"got {len(stations)}"
        )
        raise AdapterError(msg)
    doc_type = _DOC_TYPE_MAP[fields["document_type"]]
    return TransitTicketFragment.model_validate(
        {
            "documentType": doc_type,
            "sourceDocumentId": source_document_id,
            "pnr": source_document_id,
            "travelers": list(fields["travelers"]),
            "cities": list(fields["cities"]),
            "stations": [_station_from_entry(entry) for entry in stations],
        }
    )


def _build_accommodation_fragment(
    fields: ExtractedFields,
    *,
    source_document_id: str,
) -> AccommodationFragment:
    """Build an :class:`AccommodationFragment` from the extractor payload.

    The extractor doesn't emit a confirmation code, so
    ``confirmation_code`` falls back to ``source_document_id`` as a
    deterministic placeholder (same rationale as the transit ``pnr``).
    """
    accommodations = fields["accommodations"]
    if len(accommodations) < 1:
        msg = (
            f"accommodation document {source_document_id!r} needs >=1 "
            f"accommodation entry, got 0"
        )
        raise AdapterError(msg)
    doc_type = _DOC_TYPE_MAP[fields["document_type"]]
    return AccommodationFragment.model_validate(
        {
            "documentType": doc_type,
            "sourceDocumentId": source_document_id,
            "confirmationCode": source_document_id,
            "travelers": list(fields["travelers"]),
            "cities": list(fields["cities"]),
            "accommodations": [
                _accommodation_from_entry(entry) for entry in accommodations
            ],
        }
    )


def _build_supplementary_fragment(
    fields: ExtractedFields,
    *,
    source_document_id: str,
) -> SupplementaryFragment:
    """Build a :class:`SupplementaryFragment` from the extractor payload.

    The engine model requires ``len(travelers) >= 1``. Some supplementary
    payloads might (in principle) carry no travelers — the adapter raises
    :class:`AdapterError` for that case instead of trying to paper over the
    contract gap, keeping the adapter→fragment boundary crisp.
    """
    if len(fields["travelers"]) < 1:
        msg = (
            f"supplementary document {source_document_id!r} needs >=1 "
            f"traveler, got 0"
        )
        raise AdapterError(msg)
    return SupplementaryFragment.model_validate(
        {
            "documentType": "supplementary",
            "sourceDocumentId": source_document_id,
            "travelers": list(fields["travelers"]),
            "cities": list(fields["cities"]),
            "stations": [_station_from_entry(entry) for entry in fields["stations"]],
            "accommodations": [
                _accommodation_from_entry(entry) for entry in fields["accommodations"]
            ],
            "venues": [_venue_from_entry(entry) for entry in fields["venues"]],
            "prices": [_price_from_entry(entry) for entry in fields["prices"]],
            "qrCodes": list(fields["qr_codes"]),
        }
    )


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def extracted_fields_to_fragment(
    fields: ExtractedFields,
    *,
    source_document_id: str,
) -> Fragment:
    """Map one extractor :class:`ExtractedFields` payload to a :data:`Fragment`.

    Dispatches on ``fields["document_type"]``:

    - ``air_ticket`` / ``rail_ticket`` / ``bus_ticket`` →
      :class:`TransitTicketFragment`.
    - ``hotel_booking`` / ``airbnb_booking`` → :class:`AccommodationFragment`.
    - ``supplementary`` → :class:`SupplementaryFragment`.

    Raises :class:`AdapterError` if the payload's ``document_type`` is unknown
    or violates the per-variant minimum-arity guards (transit < 2 stations,
    accommodation < 1 entry, supplementary < 1 traveler).
    """
    doc_type = fields["document_type"]
    if doc_type in _TRANSIT_TYPES:
        return _build_transit_fragment(
            fields, source_document_id=source_document_id
        )
    if doc_type in _ACCOMMODATION_TYPES:
        return _build_accommodation_fragment(
            fields, source_document_id=source_document_id
        )
    if doc_type == "supplementary":
        return _build_supplementary_fragment(
            fields, source_document_id=source_document_id
        )
    msg = f"unknown document_type: {doc_type!r}"
    raise AdapterError(msg)
