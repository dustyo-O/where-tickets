"""Internal working-route models with engine-owned identity.

Our code owns stop and transit identity. The LLM may *reference* these IDs but
can never mint or reassign them — that is the structural guarantee behind the
append/identity hard gate. New IDs are minted only by the applier, monotonically.

The `Fragment` union mirrors `corpus/schema/extracted-fragment.schema.json`: a
transit ticket (air/bus/train) or a hotel booking, discriminated on
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
    "Transit",
    "WorkingRoute",
    "Leg",
    "TransitTicketFragment",
    "HotelBookingFragment",
    "Fragment",
]

# A 3-letter uppercase IATA-style city code, e.g. "ROM".
type CityCode = str


class TransitMode(StrEnum):
    """Transport mode of a transit between two stops."""

    AIR = "air"
    BUS = "bus"
    TRAIN = "train"


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
    city: str = Field(pattern=r"^[A-Z]{3}$")
    arrival_at: datetime | None = Field(default=None, alias="arrivalAt")
    departure_at: datetime | None = Field(default=None, alias="departureAt")
    travelers: list[str] = Field(default_factory=list)
    accommodations: list[Accommodation] = Field(default_factory=list)


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


class Leg(BaseModel):
    """One leg of a transit ticket."""

    model_config = ConfigDict(extra="forbid")

    from_: str = Field(alias="from", pattern=r"^[A-Z]{3}$")
    to: str = Field(pattern=r"^[A-Z]{3}$")
    departure_at: datetime = Field(alias="departureAt")
    arrival_at: datetime = Field(alias="arrivalAt")
    carrier: str | None = None
    vehicle_number: str | None = Field(default=None, alias="vehicleNumber")


class TransitTicketFragment(BaseModel):
    """A transit ticket fragment (air / bus / train)."""

    model_config = ConfigDict(extra="forbid")

    document_type: Literal["air-ticket", "bus-ticket", "train-ticket"] = Field(
        alias="documentType"
    )
    source_document_id: str = Field(alias="sourceDocumentId")
    pnr: str
    travelers: list[str]
    legs: list[Leg]


class HotelBookingFragment(BaseModel):
    """A hotel booking fragment."""

    model_config = ConfigDict(extra="forbid")

    document_type: Literal["hotel-booking"] = Field(alias="documentType")
    source_document_id: str = Field(alias="sourceDocumentId")
    confirmation_code: str = Field(alias="confirmationCode")
    travelers: list[str]
    city: str = Field(pattern=r"^[A-Z]{3}$")
    check_in_at: datetime = Field(alias="checkInAt")
    check_out_at: datetime = Field(alias="checkOutAt")
    hotel_name: str | None = Field(default=None, alias="hotelName")


type Fragment = Annotated[
    TransitTicketFragment | HotelBookingFragment,
    Field(discriminator="document_type"),
]
