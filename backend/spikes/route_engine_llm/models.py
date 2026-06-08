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
    "RouteStop",
    "Station",
    "Transit",
    "WorkingRoute",
    "TransitTicketFragment",
    "HotelBookingFragment",
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
    """A hotel stay attached to a stop."""

    model_config = ConfigDict(extra="forbid")

    check_in_at: datetime = Field(alias="checkInAt")
    check_out_at: datetime = Field(alias="checkOutAt")
    hotel_name: str | None = Field(default=None, alias="hotelName")


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

    model_config = ConfigDict(extra="forbid")

    stops: list[RouteStop] = Field(default_factory=list)
    transits: list[Transit] = Field(default_factory=list)
    next_stop_seq: int = 1
    next_transit_seq: int = 1

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


class HotelBookingFragment(BaseModel):
    """A hotel booking fragment."""

    model_config = ConfigDict(extra="forbid")

    document_type: Literal["hotel-booking"] = Field(alias="documentType")
    source_document_id: str = Field(alias="sourceDocumentId")
    confirmation_code: str = Field(alias="confirmationCode")
    travelers: list[str]
    city: str
    check_in_at: datetime = Field(alias="checkInAt")
    check_out_at: datetime = Field(alias="checkOutAt")
    hotel_name: str | None = Field(default=None, alias="hotelName")


type Fragment = Annotated[
    TransitTicketFragment | HotelBookingFragment,
    Field(discriminator="document_type"),
]
