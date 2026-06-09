"""Internal working-route models with engine-owned identity.

Our code owns stop and transit identity. The LLM may *reference* these IDs but
can never mint or reassign them — that is the structural guarantee behind the
append/identity hard gate. New IDs are minted only by the applier, monotonically.

The `Fragment` union mirrors `corpus/schema/extracted-fragment.schema.json`: a
transit ticket (air/bus/rail) or a hotel booking, discriminated on
`documentType`.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "TransitMode",
    "Accommodation",
    "FragmentAccommodation",
    "FragmentVenue",
    "Price",
    "RouteStop",
    "Station",
    "Transit",
    "UnattachedDocument",
    "Venue",
    "WorkingRoute",
    "TransitTicketFragment",
    "AccommodationFragment",
    "SupplementaryFragment",
    "Fragment",
    "city_identity",
]

# A printed city name as it appears on the source document, e.g. "Warsaw".
type CityCode = str


def city_identity(name: str) -> str:
    """Normalize a printed city name to a comparison key.

    The engine identifies cities by their printed name; two strings that
    differ only by surrounding whitespace or case (e.g. ``"Paris"`` vs
    ``"PARIS"``) refer to the same city. Anything more aggressive
    (locale folding, accent stripping) is deferred — see spec 007
    Impact & Risks "City identity collisions".
    """
    return name.strip().casefold()


class TransitMode(StrEnum):
    """Transport mode of a transit between two stops."""

    AIR = "air"
    BUS = "bus"
    RAIL = "rail"


class Station(BaseModel):
    """One transit endpoint (airport / rail station / bus terminal).

    A station entry is one *visit* to a physical place — so the same airport
    appears twice for a return trip (outbound origin + return destination).
    ``departure_at`` is set when the traveler leaves from this station;
    ``arrival_at`` is set when they arrive. A layover or return-leg turnaround
    carries both.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    city: str
    kind: Literal["airport", "rail_station", "bus_terminal"]
    identifier: str
    departure_at: datetime | None = Field(default=None, alias="departureAt")
    arrival_at: datetime | None = Field(default=None, alias="arrivalAt")


# --------------------------------------------------------------------------- #
# Working route (engine-owned identity)
# --------------------------------------------------------------------------- #


class Accommodation(BaseModel):
    """An accommodation stay attached to a stop.

    DUS-31 Slice 5: ``kind`` widens to ``{"hotel", "airbnb"}`` — airbnb rides
    the existing accommodation path; the ``kind`` value just flows through to
    the routed entry. ``identifier`` is the printed property name (what was
    previously stored in the optional ``hotel_name``).
    """

    model_config = ConfigDict(extra="forbid")

    check_in_at: datetime = Field(alias="checkInAt")
    check_out_at: datetime = Field(alias="checkOutAt")
    kind: Literal["hotel", "airbnb"]
    identifier: str


class Venue(BaseModel):
    """A venue (sightseeing / parking / other) attached to a stop.

    DUS-31 Slice 5: lives on :attr:`RouteStop.venues`. Unlike
    :class:`FragmentVenue`, a routed venue carries no ``city`` — the enclosing
    stop already names the city. The ``valid_from_at`` / ``valid_to_at``
    window is preserved from the source document for display, but the engine
    only uses ``valid_from_at`` (falling back to ``valid_to_at``) as a
    chronological anchor when classifying the venue event.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    kind: Literal["sightseeing", "parking", "other"]
    identifier: str
    valid_from_at: datetime | None = Field(default=None, alias="validFromAt")
    valid_to_at: datetime | None = Field(default=None, alias="validToAt")


class Price(BaseModel):
    """A printed price preserved on a routed or unattached document.

    Mirrors the extractor's ``PriceEntry``; carried through unchanged by the
    rules. The engine never reads it for routing — but a supplementary doc
    that gets bucketed as :class:`UnattachedDocument` keeps its prices so the
    downstream UI can surface them.
    """

    model_config = ConfigDict(extra="forbid")

    amount: float = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class UnattachedDocument(BaseModel):
    """A supplementary document with no routable place.

    DUS-31 Slice 5: appended to :attr:`WorkingRoute.unattached_documents` so
    the downstream UI can still surface the document on the trip even though
    it does not change the route's sequence of cities. Strictly invisible to
    ``scoring.final_route_match`` / ``identity_preserved`` /
    ``ordering_consistent``.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source_document_id: str = Field(alias="sourceDocumentId")
    document_type: Literal["supplementary"] = Field(alias="documentType")
    prices: list[Price] = Field(default_factory=list)
    qr_codes: list[str] = Field(default_factory=list, alias="qrCodes")


class RouteStop(BaseModel):
    """A single city stop in the route. `id` is engine-owned and stable."""

    model_config = ConfigDict(extra="forbid")

    id: str
    city: str
    arrival_at: datetime | None = Field(default=None, alias="arrivalAt")
    departure_at: datetime | None = Field(default=None, alias="departureAt")
    travelers: list[str] = Field(default_factory=list)
    accommodations: list[Accommodation] = Field(default_factory=list)
    # Stations contributed to this stop by the transits / source PDFs that
    # touched the city. The expected-route schema does NOT carry this field
    # in DUS-31 Slice 3 — scoring.final_route_match strips it so the working
    # route can grow station detail without breaking the comparison.
    stations: list[Station] = Field(default_factory=list)
    # Venues (sightseeing / parking / other) contributed by supplementary
    # documents that name this stop's city. DUS-31 Slice 5: the order is
    # insertion-then-(kind, identifier) dedupe (mirrors how `stations[]` was
    # appended in Slice 3). The expected-route schema CAN carry this field
    # (it's optional / defaulted there), but scoring.final_route_match
    # ignores it so the 192-corpus stays comparison-stable.
    venues: list[Venue] = Field(default_factory=list)


class Transit(BaseModel):
    """A leg between two stops, identified by engine-owned stop IDs."""

    model_config = ConfigDict(extra="forbid")

    id: str
    from_stop_id: str = Field(alias="fromStopId")
    to_stop_id: str = Field(alias="toStopId")
    mode: TransitMode
    departure_at: datetime = Field(alias="departureAt")
    arrival_at: datetime = Field(alias="arrivalAt")
    travelers: list[str] = Field(default_factory=list)
    source_fragment_id: str = Field(alias="sourceFragmentId")
    # Which station in the from/to city the transit actually departs/arrives
    # at. Not part of the expected-route comparison in Slice 3 — see
    # scoring.final_route_match for the strip.
    origin_station: Station | None = Field(default=None, alias="originStation")
    destination_station: Station | None = Field(
        default=None, alias="destinationStation"
    )


class WorkingRoute(BaseModel):
    """The engine's internal route: ordered stops + transits, with ID counters.

    `_next_stop_seq` / `_next_transit_seq` only ever increase. IDs minted from
    them are never reused or reassigned, so an existing stop keeps its identity
    for the lifetime of the route.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    stops: list[RouteStop] = Field(default_factory=list)
    transits: list[Transit] = Field(default_factory=list)
    next_stop_seq: int = 1
    next_transit_seq: int = 1
    # Supplementary documents that carry no routable place. Strictly invisible
    # to scoring (final_route_match / identity_preserved / ordering_consistent)
    # — added in DUS-31 Slice 5 so the downstream UI keeps these documents on
    # the trip without the engine routing on them.
    unattached_documents: list[UnattachedDocument] = Field(
        default_factory=list, alias="unattachedDocuments"
    )

    # -- ID minting -------------------------------------------------------- #

    def mint_stop_id(self) -> str:
        """Return the next fresh `stop-N` id and advance the counter."""
        stop_id = f"stop-{self.next_stop_seq}"
        self.next_stop_seq += 1
        return stop_id

    def mint_transit_id(self) -> str:
        """Return the next fresh `transit-N` id and advance the counter."""
        transit_id = f"transit-{self.next_transit_seq}"
        self.next_transit_seq += 1
        return transit_id

    # -- Lookups ----------------------------------------------------------- #

    def stop_by_id(self, stop_id: str) -> RouteStop | None:
        """Return the stop with `stop_id`, or None if absent."""
        return next((s for s in self.stops if s.id == stop_id), None)

    def has_stop(self, stop_id: str) -> bool:
        """Whether a stop with `stop_id` currently exists."""
        return any(s.id == stop_id for s in self.stops)

    def stop_index(self, stop_id: str) -> int | None:
        """Return the ordered position of `stop_id`, or None if absent."""
        return next(
            (i for i, s in enumerate(self.stops) if s.id == stop_id),
            None,
        )

    def stop_ids(self) -> list[str]:
        """Stop IDs in current route order."""
        return [s.id for s in self.stops]

    # -- Ordering ---------------------------------------------------------- #

    def insert_stop(self, stop: RouteStop, after_id: str | None) -> None:
        """Splice `stop` in immediately after `after_id`, or at the front.

        `after_id` of None means prepend at index 0. The caller is responsible
        for validating `after_id` exists when it is not None.
        """
        if after_id is None:
            self.stops.insert(0, stop)
            return
        index = self.stop_index(after_id)
        if index is None:
            msg = f"cannot insert after unknown stop id: {after_id!r}"
            raise ValueError(msg)
        self.stops.insert(index + 1, stop)


# --------------------------------------------------------------------------- #
# Input fragments (mirror of corpus extracted-fragment schema)
# --------------------------------------------------------------------------- #


class TransitTicketFragment(BaseModel):
    """A transit ticket fragment (air / bus / rail).

    Endpoints arrive in compact ``stations[]`` form (one entry per visit to a
    physical station): an origin carries ``departure_at`` only, a terminus
    carries ``arrival_at`` only, a layover or return-leg turnaround carries
    both. The algorithmic rules derive ordered legs from this list — see
    ``spikes.route_engine_algorithmic.rules._legs_from_stations``.
    """

    model_config = ConfigDict(extra="forbid")

    document_type: Literal["air-ticket", "bus-ticket", "rail-ticket"] = Field(
        alias="documentType"
    )
    source_document_id: str = Field(alias="sourceDocumentId")
    pnr: str
    travelers: list[str]
    cities: list[str] = Field(min_length=1)
    stations: list[Station] = Field(min_length=2)


class FragmentAccommodation(BaseModel):
    """One accommodation entry inside an :class:`AccommodationFragment`.

    Mirrors the extractor's compact ``accommodations[]`` shape: a per-entry
    city + kind + identifier + required check-in / check-out datetimes. The
    surrounding fragment's ``cities[]`` is the deduplicated set of cities the
    document mentions; each accommodation entry independently names the city
    it belongs to.

    DUS-31 Slice 5: ``kind`` widens to ``{"hotel", "airbnb"}`` so airbnb
    bookings ride the existing accommodation pipeline.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    city: str
    kind: Literal["hotel", "airbnb"]
    identifier: str
    check_in_at: datetime = Field(alias="checkInAt")
    check_out_at: datetime = Field(alias="checkOutAt")


class FragmentVenue(BaseModel):
    """One venue entry inside a :class:`SupplementaryFragment`.

    DUS-31 Slice 5: only supplementary documents carry venues. Transit and
    accommodation fragments deliberately do NOT carry a ``venues[]`` list —
    the supplementary doc type is the single carrier so the routing decision
    stays simple (one fragment-level handler, one new event role).

    ``kind`` discriminates the venue subtype (sightseeing / parking / other).
    ``valid_from_at`` / ``valid_to_at`` are optional — when both are absent
    the rules attach the venue without a chronological anchor.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    city: str
    kind: Literal["sightseeing", "parking", "other"]
    identifier: str
    valid_from_at: datetime | None = Field(default=None, alias="validFromAt")
    valid_to_at: datetime | None = Field(default=None, alias="validToAt")


class AccommodationFragment(BaseModel):
    """A lodging-document fragment (``hotel-booking`` / ``airbnb-booking``).

    DUS-31 Slice 4: replaces the single-shot ``city`` / ``check_in_at`` /
    ``check_out_at`` / ``hotel_name`` fields with a compact
    ``accommodations[]`` plus ``cities[]``. Each entry inside
    ``accommodations[]`` carries its own city, kind, identifier and dates so
    a single fragment can describe multiple stays (e.g. a multi-night chain
    booking spanning two cities). Today's generator emits exactly one entry
    per fragment; Slice 6's adapter / Slice 7+ scenarios may emit several.

    DUS-31 Slice 5: ``document_type`` widens to include
    ``"airbnb-booking"`` — airbnb routes identically to hotel; the
    discriminator value just flows through to the routed accommodation.
    """

    model_config = ConfigDict(extra="forbid")

    document_type: Literal["hotel-booking", "airbnb-booking"] = Field(
        alias="documentType"
    )
    source_document_id: str = Field(alias="sourceDocumentId")
    confirmation_code: str = Field(alias="confirmationCode")
    travelers: list[str]
    cities: list[str] = Field(min_length=1)
    accommodations: list[FragmentAccommodation] = Field(min_length=1)


class SupplementaryFragment(BaseModel):
    """A supplementary document fragment (voucher, sightseeing ticket, etc.).

    DUS-31 Slice 5: the third fragment variant. Every routable list is
    optional / defaulted because a supplementary doc may carry any
    combination of routable places — or none at all.

    Routing decision (see ``spikes.route_engine_algorithmic.rules``):

    - If ``stations[]`` / ``accommodations[]`` / ``venues[]`` is non-empty,
      those entries are routed by their respective primary handlers.
    - Otherwise (and only otherwise) the fragment becomes one
      :class:`UnattachedDocument` on the working route. A supplementary
      with at least one routable place is NEVER also unattached.

    ``stations[]`` on a supplementary doc is treated as a list of independent
    station events (CREATE-or-ENRICH per entry) — pairing into legs is
    transit-ticket-only.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    document_type: Literal["supplementary"] = Field(alias="documentType")
    source_document_id: str = Field(alias="sourceDocumentId")
    travelers: list[str] = Field(min_length=1)
    cities: list[str] = Field(default_factory=list)
    stations: list[Station] = Field(default_factory=list)
    accommodations: list[FragmentAccommodation] = Field(default_factory=list)
    venues: list[FragmentVenue] = Field(default_factory=list)
    prices: list[Price] = Field(default_factory=list)
    qr_codes: list[str] = Field(default_factory=list, alias="qrCodes")


type Fragment = Annotated[
    TransitTicketFragment | AccommodationFragment | SupplementaryFragment,
    Field(discriminator="document_type"),
]
