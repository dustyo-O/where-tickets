"""Convert a composed route into per-document fragments.

A scenario starts as a fully-known route (city sequence + per-hop mode + per-hop
timestamps + traveler list + hotel decisions). The fragmenter shatters that
route into the documents a traveler would actually receive:

- A multi-hop transit on the same mode and contiguous carriers becomes ONE
  ticket fragment whose ``stations[]`` carries the journey's endpoints in
  compact form (DUS-31 Slice 3 — see ``corpus/schema/extracted-fragment.schema.json``).
- A mode change forces a new ticket fragment.
- Each stopover with a hotel becomes one hotel-booking fragment.

The fragmenter also returns the canonical ``expected-route`` payload that the
engine should reconstruct from the (re-ordered) fragments.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .cities import accommodation_identifier, station_identifier, station_kind

EPOCH = datetime(2027, 3, 1, tzinfo=timezone.utc)
TRAVEL_HOURS = 3
STOPOVER_HOURS = 18  # gives time for a hotel night
SHORT_LAYOVER_HOURS = 5  # used when no hotel

CARRIERS_BY_MODE: dict[str, tuple[str, ...]] = {
    "air": ("LO", "BA", "LH", "AF", "KL"),
    "bus": ("FlixBus", "Eurolines", "RegioJet"),
    "rail": ("DB", "SNCF", "Trenitalia", "OBB"),
}

HOTEL_NAMES: tuple[str, ...] = (
    "Grand Hotel", "Riverside Inn", "Old Town Hostel", "Plaza Suites",
    "Central Lodge", "Harbor View", "City Garden", "Skyline Hotel",
)


def _isoformat(dt: datetime) -> str:
    # Always emit with explicit "Z" suffix for stable, schema-friendly output.
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _travelers(pax: int) -> list[str]:
    return [f"traveler-{i + 1}" for i in range(pax)]


@dataclass(slots=True)
class Hop:
    from_city: str
    to_city: str
    mode: str
    carrier: str
    vehicle: str
    depart: datetime
    arrive: datetime


def _build_hops(
    cities: list[str], primary_mode: str, hotels: bool, rng: random.Random
) -> list[Hop]:
    """Build the chronological hops with timing baked in."""
    hops: list[Hop] = []
    cursor = EPOCH
    # First hop always uses primary_mode; subsequent hops cycle modes to force
    # mode changes (and therefore separate ticket fragments). One mode swap per
    # 3 hops keeps multi-leg tickets common.
    modes_cycle = [primary_mode, primary_mode, primary_mode]
    other_modes = [m for m in CARRIERS_BY_MODE.keys() if m != primary_mode]
    rng_local = random.Random(rng.random())
    if other_modes:
        modes_cycle.append(rng_local.choice(other_modes))

    # Carrier "block": pick a carrier once per mode-run so contiguous same-mode
    # hops share a carrier and collapse into one multi-leg ticket fragment.
    last_mode: str | None = None
    carrier_for_block = ""
    for hop_idx, (origin, dest) in enumerate(zip(cities[:-1], cities[1:])):
        mode = modes_cycle[hop_idx % len(modes_cycle)]
        if mode != last_mode:
            carrier_for_block = CARRIERS_BY_MODE[mode][hop_idx % len(CARRIERS_BY_MODE[mode])]
            last_mode = mode
        carrier = carrier_for_block
        vehicle = f"{carrier[:2].upper()}{100 + hop_idx}"
        depart = cursor
        arrive = depart + timedelta(hours=TRAVEL_HOURS)
        hops.append(
            Hop(
                from_city=origin,
                to_city=dest,
                mode=mode,
                carrier=carrier,
                vehicle=vehicle,
                depart=depart,
                arrive=arrive,
            )
        )
        # Cursor for next departure depends on whether we'll stop overnight.
        layover = STOPOVER_HOURS if hotels else SHORT_LAYOVER_HOURS
        cursor = arrive + timedelta(hours=layover)
    return hops


def _group_hops_into_tickets(hops: list[Hop]) -> list[list[Hop]]:
    """Group contiguous same-mode same-carrier hops into one ticket fragment."""
    groups: list[list[Hop]] = []
    for hop in hops:
        if groups and groups[-1][-1].mode == hop.mode and groups[-1][-1].carrier == hop.carrier:
            groups[-1].append(hop)
        else:
            groups.append([hop])
    return groups


def _doc_type_for_mode(mode: str) -> str:
    return {"air": "air-ticket", "bus": "bus-ticket", "rail": "rail-ticket"}[mode]


def _cities_in_first_seen_order(hops: list[Hop]) -> list[str]:
    """Return every city the hop group touches, deduped, in first-seen order."""
    cities: list[str] = []
    seen: set[str] = set()
    for hop in hops:
        for city in (hop.from_city, hop.to_city):
            if city not in seen:
                cities.append(city)
                seen.add(city)
    return cities


def _stations_from_hops(hops: list[Hop], mode: str) -> list[dict[str, Any]]:
    """Reshape chronological hops into the compact ``stations[]`` payload.

    For a 1-hop A->B group: two entries (A dep, B arr).
    For a layover A->B->C: three entries (A dep, B arr+dep, C arr) — the
    middle ``B`` entry collapses the contiguous (arr at B, dep from B) pair.
    For a return A->B->A: three entries (A dep, B arr+dep, A arr) — the two
    A visits stay distinct because they are not contiguous in hop order.

    The fold rule: walk the hop chain to build a chronological sequence of
    ``(kind, time, city)`` events, then fold any two adjacent events that
    are ``(arr at S, dep from S)`` for the same city S into a single entry
    carrying both timestamps. Two same-city events that are NOT adjacent
    (e.g. A->B->A's two A visits) stay as separate entries.
    """
    events: list[tuple[str, datetime, str]] = []
    for hop in hops:
        events.append(("departure", hop.depart, hop.from_city))
        events.append(("arrival", hop.arrive, hop.to_city))

    stations: list[dict[str, Any]] = []
    kind = station_kind(mode)
    i = 0
    while i < len(events):
        event_kind, event_time, event_city = events[i]
        entry: dict[str, Any] = {
            "city": event_city,
            "kind": kind,
            "identifier": station_identifier(event_city, mode),
        }
        if event_kind == "departure":
            entry["departureAt"] = _isoformat(event_time)
        else:
            entry["arrivalAt"] = _isoformat(event_time)

        # Fold (arr at S, dep from S) when this entry is an arrival and the
        # very next event is a departure from the SAME city.
        if (
            event_kind == "arrival"
            and i + 1 < len(events)
            and events[i + 1][0] == "departure"
            and events[i + 1][2] == event_city
        ):
            entry["departureAt"] = _isoformat(events[i + 1][1])
            i += 2
        else:
            i += 1
        stations.append(entry)
    return stations


def build_fragments_and_route(
    cities: list[str],
    pax: int,
    primary_mode: str,
    hotels: bool,
    scenario_slug: str,
    rng: random.Random,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return (chronological_fragments, expected_route)."""
    travelers = _travelers(pax)
    hops = _build_hops(cities, primary_mode, hotels, rng)
    ticket_groups = _group_hops_into_tickets(hops)

    chronological_fragments: list[dict[str, Any]] = []
    transits: list[dict[str, Any]] = []

    # Build ticket fragments first (one fragment per group of contiguous hops).
    for group_idx, group in enumerate(ticket_groups):
        mode = group[0].mode
        fragment_id = f"{scenario_slug}-tkt-{group_idx + 1:02d}"
        stations_payload = _stations_from_hops(group, mode)
        cities_payload = _cities_in_first_seen_order(group)
        for hop in group:
            transits.append(
                {
                    "from": hop.from_city,
                    "to": hop.to_city,
                    "mode": mode,
                    "departureAt": _isoformat(hop.depart),
                    "arrivalAt": _isoformat(hop.arrive),
                    "travelers": list(travelers),
                    "sourceFragmentId": fragment_id,
                }
            )
        chronological_fragments.append(
            {
                "documentType": _doc_type_for_mode(mode),
                "sourceDocumentId": fragment_id,
                "pnr": f"PNR{group_idx + 1:03d}{scenario_slug[:3].upper()}",
                "travelers": list(travelers),
                "cities": cities_payload,
                "stations": stations_payload,
            }
        )

    # Build stops (per visited city slot) and (optionally) hotel fragments.
    stops: list[dict[str, Any]] = []
    # The first city: only a "departure" (no arrival).
    first_city = cities[0]
    stops.append(
        {
            "city": first_city,
            "departureAt": _isoformat(hops[0].depart),
            "travelers": list(travelers),
        }
    )
    # Intermediate cities: each has arrival + departure of the next hop.
    for idx in range(1, len(cities) - 1):
        arrive_hop = hops[idx - 1]
        depart_hop = hops[idx]
        stop: dict[str, Any] = {
            "city": cities[idx],
            "arrivalAt": _isoformat(arrive_hop.arrive),
            "departureAt": _isoformat(depart_hop.depart),
            "travelers": list(travelers),
        }
        if hotels:
            hotel_name = HOTEL_NAMES[idx % len(HOTEL_NAMES)]
            identifier = accommodation_identifier(hotel_name)
            stop["accommodations"] = [
                {
                    "checkInAt": _isoformat(arrive_hop.arrive),
                    "checkOutAt": _isoformat(depart_hop.depart),
                    "kind": "hotel",
                    "identifier": identifier,
                }
            ]
            # And a matching accommodation fragment (DUS-31 Slice 4: compact
            # accommodations[] shape, one entry per generator-emitted fragment).
            hotel_fragment_id = f"{scenario_slug}-htl-{idx:02d}"
            chronological_fragments.append(
                {
                    "documentType": "hotel-booking",
                    "sourceDocumentId": hotel_fragment_id,
                    "confirmationCode": f"HTL{idx:03d}{scenario_slug[:3].upper()}",
                    "travelers": list(travelers),
                    "cities": [cities[idx]],
                    "accommodations": [
                        {
                            "city": cities[idx],
                            "kind": "hotel",
                            "identifier": identifier,
                            "checkInAt": _isoformat(arrive_hop.arrive),
                            "checkOutAt": _isoformat(depart_hop.depart),
                        }
                    ],
                }
            )
        stops.append(stop)
    # Final city: only arrival.
    final_hop = hops[-1]
    stops.append(
        {
            "city": cities[-1],
            "arrivalAt": _isoformat(final_hop.arrive),
            "travelers": list(travelers),
        }
    )

    # Order chronological_fragments by the timestamp of the first leg / check-in.
    def fragment_sort_key(fragment: dict[str, Any]) -> str:
        if fragment["documentType"] == "hotel-booking":
            # Earliest checkInAt across the compact accommodations[].
            check_ins = [a["checkInAt"] for a in fragment["accommodations"]]
            return min(check_ins) + "-htl"
        # Transit fragments: pick the earliest departureAt across the compact
        # stations[]. Always at least one station has a departureAt — every
        # transit ticket has an origin event.
        departures = [
            s["departureAt"] for s in fragment["stations"] if "departureAt" in s
        ]
        return min(departures) + "-tkt"

    chronological_fragments.sort(key=fragment_sort_key)

    expected_route = {
        "travelers": list(travelers),
        "stops": stops,
        "transits": transits,
    }
    return chronological_fragments, expected_route
