"""Per-PDF building blocks for integration trip bundles.

Each primitive describes exactly one PDF the composer will eventually emit.
The dataclasses are pure value objects — no side effects, no I/O — so a trip
catalogue entry can stack them into a list and hand the list to
:func:`composer.compose_trip`.

The primitive types correspond 1:1 to the PDF templates in
``corpus/pdf/generator/templates/``:

- :func:`air_leg` / :func:`rail_leg` / :func:`bus_leg`
  → ``air-ticket.html.j2`` / ``rail-ticket.html.j2`` / ``bus-ticket.html.j2``
  (single-leg, 2 stations).
- :func:`air_return` / :func:`rail_return` / :func:`bus_return`
  → same templates with compact-form return (3 stations: A → B → A).
- :func:`hotel_stay` → ``hotel-booking.html.j2``.
- :func:`airbnb_stay` → ``airbnb-booking.html.j2``.
- :func:`supplementary_venue` → ``supplementary.html.j2`` (one venue with a city).
- :func:`supplementary_no_location` → ``supplementary.html.j2`` (zero venues, zero
  stations, zero accommodations — produces an ``unattached_documents`` entry).
- :func:`unreadable_pdf` → empty PDF with no extractable text; the runner is
  expected to flag the document ``expect_unreadable: true`` and the trip's
  expected route is built from the rest.

The composer (see :mod:`composer`) reads the primitive fields directly; it does
not inspect their type. All datetimes are :class:`datetime.datetime` in UTC
(treat-printed-as-UTC, per the engine corpus convention). The composer formats
them into ISO-local strings for the per-PDF expected-fields JSON, and into
ISO-Z strings for the trip's expected-route.json.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

__all__ = [
    "AccommodationPrimitive",
    "AnyPrimitive",
    "Rendering",
    "StationKind",
    "SupplementaryPrimitive",
    "TransitMode",
    "TransitPrimitive",
    "UnreadablePrimitive",
    "VenueKind",
    "air_leg",
    "air_return",
    "airbnb_stay",
    "bus_leg",
    "bus_return",
    "hotel_stay",
    "rail_leg",
    "rail_return",
    "supplementary_no_location",
    "supplementary_venue",
    "unreadable_pdf",
]


type Rendering = Literal["text", "rasterized"]
type TransitMode = Literal["air", "rail", "bus"]
type StationKind = Literal["airport", "rail_station", "bus_terminal"]
type VenueKind = Literal["sightseeing", "parking", "other"]


_STATION_KIND_BY_MODE: dict[TransitMode, StationKind] = {
    "air": "airport",
    "rail": "rail_station",
    "bus": "bus_terminal",
}

_DOCUMENT_TYPE_BY_MODE: dict[TransitMode, str] = {
    "air": "air_ticket",
    "rail": "rail_ticket",
    "bus": "bus_ticket",
}

_QR_PREFIX_BY_MODE: dict[TransitMode, str] = {
    "air": "AIRTKT",
    "rail": "RAILTKT",
    "bus": "BUSTKT",
}


@dataclass(frozen=True, slots=True)
class _StationLeg:
    """One stop in a transit primitive's chronological station list."""

    city: str
    identifier: str
    departure_at: datetime | None = None
    arrival_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TransitPrimitive:
    """One transit PDF (air / rail / bus, one-leg or compact-return)."""

    mode: TransitMode
    travelers: tuple[str, ...]
    stations: tuple[_StationLeg, ...]
    price_eur: float
    pnr: str
    # Cities printed on the document in their printed surface form. For a
    # straight one-leg ticket this is `[origin, destination]`; for a compact
    # return it's `[origin, destination]` again (the duplicate origin stop
    # contributes no new city to the "cities" header).
    cities: tuple[str, ...]
    rendering: Rendering = "text"

    @property
    def document_type(self) -> str:
        """Extractor-shaped snake_case document type (``"air_ticket"`` etc.)."""
        return _DOCUMENT_TYPE_BY_MODE[self.mode]

    @property
    def station_kind(self) -> StationKind:
        """The ``Station.kind`` value used on every station entry."""
        return _STATION_KIND_BY_MODE[self.mode]

    @property
    def qr_prefix(self) -> str:
        """Per-mode QR-payload prefix, used by :mod:`composer` to mint QR codes."""
        return _QR_PREFIX_BY_MODE[self.mode]


@dataclass(frozen=True, slots=True)
class AccommodationPrimitive:
    """One accommodation booking PDF (hotel or airbnb)."""

    kind: Literal["hotel", "airbnb"]
    city: str
    identifier: str
    check_in_at: datetime
    check_out_at: datetime
    travelers: tuple[str, ...]
    price_eur: float
    confirmation_code: str
    rendering: Rendering = "text"

    @property
    def document_type(self) -> str:
        """Snake_case document type the templates and runner expect."""
        return "hotel_booking" if self.kind == "hotel" else "airbnb_booking"

    @property
    def qr_prefix(self) -> str:
        """Per-kind QR prefix; mirrors the layer-1 generator's convention."""
        return "HOTBKG" if self.kind == "hotel" else "ABNB"


@dataclass(frozen=True, slots=True)
class SupplementaryPrimitive:
    """One supplementary PDF (sightseeing / parking / other).

    Two flavours: with-city (``venue_kind``, ``venue_city``, ``venue_identifier``
    set) — the engine routes the venue onto that city's stop.
    Or no-location (``venue_kind=None``) — the engine adds an
    ``unattached_documents`` entry; the PDF still ships travelers + prices +
    qr_codes for downstream display.
    """

    travelers: tuple[str, ...]
    price_eur: float
    reference_code: str
    venue_kind: VenueKind | None = None
    venue_city: str | None = None
    venue_identifier: str | None = None
    valid_from_at: datetime | None = None
    valid_to_at: datetime | None = None
    rendering: Rendering = "text"

    @property
    def document_type(self) -> str:
        return "supplementary"

    @property
    def qr_prefix(self) -> str:
        if self.venue_kind == "sightseeing":
            return "SUPP-SIGHT"
        if self.venue_kind == "parking":
            return "SUPP-PARK"
        return "SUPP-OTHER"


@dataclass(frozen=True, slots=True)
class UnreadablePrimitive:
    """A PDF that the extractor is expected to fail on.

    Composer emits a single-page PDF with no extractable text (no template
    pull). The runner marks the document ``expect_unreadable: true`` and the
    trip's expected-route is computed from every other primitive in order.
    The unreadable primitive contributes NO city / station / accommodation /
    venue events to the expected route.
    """

    # Useful for ordering the manifest entry — composer drops this primitive
    # from event derivation but keeps it in `manifest.documents[]`.
    placeholder_name: str = "unreadable"
    # Carried for parity with other primitives even though every routable
    # array is empty.
    travelers: tuple[str, ...] = field(default_factory=tuple)


type AnyPrimitive = (
    TransitPrimitive
    | AccommodationPrimitive
    | SupplementaryPrimitive
    | UnreadablePrimitive
)


# --------------------------------------------------------------------------- #
# Builder helpers
# --------------------------------------------------------------------------- #


def air_leg(
    *,
    from_city: str,
    to_city: str,
    departure_at: datetime,
    arrival_at: datetime,
    travelers: tuple[str, ...],
    from_identifier: str,
    to_identifier: str,
    price_eur: float = 145.00,
    pnr: str = "PNR-AL-1",
    rendering: Rendering = "text",
) -> TransitPrimitive:
    """Build a one-leg air ticket primitive (2 stations)."""
    return TransitPrimitive(
        mode="air",
        travelers=travelers,
        stations=(
            _StationLeg(city=from_city, identifier=from_identifier, departure_at=departure_at),
            _StationLeg(city=to_city, identifier=to_identifier, arrival_at=arrival_at),
        ),
        cities=(from_city, to_city),
        price_eur=price_eur,
        pnr=pnr,
        rendering=rendering,
    )


def air_return(
    *,
    from_city: str,
    to_city: str,
    outbound_departure_at: datetime,
    outbound_arrival_at: datetime,
    return_departure_at: datetime,
    return_arrival_at: datetime,
    travelers: tuple[str, ...],
    from_identifier: str,
    to_identifier: str,
    price_eur: float = 198.00,
    pnr: str = "PNR-AR-1",
    rendering: Rendering = "text",
) -> TransitPrimitive:
    """Build a compact-form return air ticket primitive (3 stations)."""
    return TransitPrimitive(
        mode="air",
        travelers=travelers,
        stations=(
            _StationLeg(
                city=from_city,
                identifier=from_identifier,
                departure_at=outbound_departure_at,
            ),
            _StationLeg(
                city=to_city,
                identifier=to_identifier,
                arrival_at=outbound_arrival_at,
                departure_at=return_departure_at,
            ),
            _StationLeg(
                city=from_city,
                identifier=from_identifier,
                arrival_at=return_arrival_at,
            ),
        ),
        cities=(from_city, to_city),
        price_eur=price_eur,
        pnr=pnr,
        rendering=rendering,
    )


def rail_leg(
    *,
    from_city: str,
    to_city: str,
    departure_at: datetime,
    arrival_at: datetime,
    travelers: tuple[str, ...],
    from_identifier: str,
    to_identifier: str,
    price_eur: float = 65.00,
    pnr: str = "PNR-RL-1",
    rendering: Rendering = "text",
) -> TransitPrimitive:
    """Build a one-leg rail ticket primitive (2 stations)."""
    return TransitPrimitive(
        mode="rail",
        travelers=travelers,
        stations=(
            _StationLeg(city=from_city, identifier=from_identifier, departure_at=departure_at),
            _StationLeg(city=to_city, identifier=to_identifier, arrival_at=arrival_at),
        ),
        cities=(from_city, to_city),
        price_eur=price_eur,
        pnr=pnr,
        rendering=rendering,
    )


def rail_return(
    *,
    from_city: str,
    to_city: str,
    outbound_departure_at: datetime,
    outbound_arrival_at: datetime,
    return_departure_at: datetime,
    return_arrival_at: datetime,
    travelers: tuple[str, ...],
    from_identifier: str,
    to_identifier: str,
    price_eur: float = 95.00,
    pnr: str = "PNR-RR-1",
    rendering: Rendering = "text",
) -> TransitPrimitive:
    """Build a compact-form return rail ticket primitive (3 stations)."""
    return TransitPrimitive(
        mode="rail",
        travelers=travelers,
        stations=(
            _StationLeg(
                city=from_city,
                identifier=from_identifier,
                departure_at=outbound_departure_at,
            ),
            _StationLeg(
                city=to_city,
                identifier=to_identifier,
                arrival_at=outbound_arrival_at,
                departure_at=return_departure_at,
            ),
            _StationLeg(
                city=from_city,
                identifier=from_identifier,
                arrival_at=return_arrival_at,
            ),
        ),
        cities=(from_city, to_city),
        price_eur=price_eur,
        pnr=pnr,
        rendering=rendering,
    )


def bus_leg(
    *,
    from_city: str,
    to_city: str,
    departure_at: datetime,
    arrival_at: datetime,
    travelers: tuple[str, ...],
    from_identifier: str,
    to_identifier: str,
    price_eur: float = 35.00,
    pnr: str = "PNR-BL-1",
    rendering: Rendering = "text",
) -> TransitPrimitive:
    """Build a one-leg bus ticket primitive (2 stations)."""
    return TransitPrimitive(
        mode="bus",
        travelers=travelers,
        stations=(
            _StationLeg(city=from_city, identifier=from_identifier, departure_at=departure_at),
            _StationLeg(city=to_city, identifier=to_identifier, arrival_at=arrival_at),
        ),
        cities=(from_city, to_city),
        price_eur=price_eur,
        pnr=pnr,
        rendering=rendering,
    )


def bus_return(
    *,
    from_city: str,
    to_city: str,
    outbound_departure_at: datetime,
    outbound_arrival_at: datetime,
    return_departure_at: datetime,
    return_arrival_at: datetime,
    travelers: tuple[str, ...],
    from_identifier: str,
    to_identifier: str,
    price_eur: float = 55.00,
    pnr: str = "PNR-BR-1",
    rendering: Rendering = "text",
) -> TransitPrimitive:
    """Build a compact-form return bus ticket primitive (3 stations)."""
    return TransitPrimitive(
        mode="bus",
        travelers=travelers,
        stations=(
            _StationLeg(
                city=from_city,
                identifier=from_identifier,
                departure_at=outbound_departure_at,
            ),
            _StationLeg(
                city=to_city,
                identifier=to_identifier,
                arrival_at=outbound_arrival_at,
                departure_at=return_departure_at,
            ),
            _StationLeg(
                city=from_city,
                identifier=from_identifier,
                arrival_at=return_arrival_at,
            ),
        ),
        cities=(from_city, to_city),
        price_eur=price_eur,
        pnr=pnr,
        rendering=rendering,
    )


def hotel_stay(
    *,
    city: str,
    check_in_at: datetime,
    check_out_at: datetime,
    travelers: tuple[str, ...],
    identifier: str,
    price_eur: float = 220.00,
    confirmation_code: str = "HTL-CONF-1",
    rendering: Rendering = "text",
) -> AccommodationPrimitive:
    """Build a hotel-booking primitive."""
    return AccommodationPrimitive(
        kind="hotel",
        city=city,
        identifier=identifier,
        check_in_at=check_in_at,
        check_out_at=check_out_at,
        travelers=travelers,
        price_eur=price_eur,
        confirmation_code=confirmation_code,
        rendering=rendering,
    )


def airbnb_stay(
    *,
    city: str,
    check_in_at: datetime,
    check_out_at: datetime,
    travelers: tuple[str, ...],
    identifier: str,
    price_eur: float = 180.00,
    confirmation_code: str = "ABN-CONF-1",
    rendering: Rendering = "text",
) -> AccommodationPrimitive:
    """Build an airbnb-booking primitive."""
    return AccommodationPrimitive(
        kind="airbnb",
        city=city,
        identifier=identifier,
        check_in_at=check_in_at,
        check_out_at=check_out_at,
        travelers=travelers,
        price_eur=price_eur,
        confirmation_code=confirmation_code,
        rendering=rendering,
    )


def supplementary_venue(
    *,
    city: str,
    kind: VenueKind,
    identifier: str,
    travelers: tuple[str, ...],
    valid_from_at: datetime | None = None,
    valid_to_at: datetime | None = None,
    price_eur: float = 28.00,
    reference_code: str = "SUPP-REF-1",
    rendering: Rendering = "text",
) -> SupplementaryPrimitive:
    """Build a supplementary primitive that carries one venue at a city."""
    return SupplementaryPrimitive(
        travelers=travelers,
        price_eur=price_eur,
        reference_code=reference_code,
        venue_kind=kind,
        venue_city=city,
        venue_identifier=identifier,
        valid_from_at=valid_from_at,
        valid_to_at=valid_to_at,
        rendering=rendering,
    )


def supplementary_no_location(
    *,
    travelers: tuple[str, ...],
    price_eur: float = 12.00,
    reference_code: str = "SUPP-REF-1",
    rendering: Rendering = "text",
) -> SupplementaryPrimitive:
    """Build a supplementary primitive with no routable place."""
    return SupplementaryPrimitive(
        travelers=travelers,
        price_eur=price_eur,
        reference_code=reference_code,
        venue_kind=None,
        venue_city=None,
        venue_identifier=None,
        valid_from_at=None,
        valid_to_at=None,
        rendering=rendering,
    )


def unreadable_pdf(
    *,
    placeholder_name: str = "unreadable",
    travelers: tuple[str, ...] = (),
) -> UnreadablePrimitive:
    """Build an unreadable PDF primitive (extractor failure expected)."""
    return UnreadablePrimitive(placeholder_name=placeholder_name, travelers=travelers)
