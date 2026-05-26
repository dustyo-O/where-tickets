"""System prompt + the operation tool schema for the LLM route engine.

The model acts as the route engine: it receives the current route (stops carry
our engine-owned IDs) and ONE new document fragment, and must return a list of
operations that APPEND to / ENRICH the existing route. Our applier
(``operations.apply``) owns identity — the model can only reference existing
stops by their given IDs, never mint or reassign them.

The static system prompt and the tool schema are marked for prompt caching
(``cache_control``), so the (large, fixed) instructions + schema are billed once
per model run rather than on every per-fragment call. Render order is
``tools`` -> ``system`` -> ``messages``: both cached blocks sit in the stable
prefix, and the volatile per-call payload (current route + fragment) goes in the
user message after the cache boundary.

The tool's ``input_schema`` is derived from the Pydantic op union in
``operations.py`` via ``TypeAdapter`` so it mirrors the op set exactly and stays
in sync by construction.
"""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from spikes.route_engine_llm.operations import Op

__all__ = [
    "TOOL_NAME",
    "SYSTEM_PROMPT",
    "build_system_blocks",
    "build_tool",
    "build_tool_choice",
]

# The single tool the model is forced to call.
TOOL_NAME = "emit_route_operations"


# --------------------------------------------------------------------------- #
# System prompt (static — cached)
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT = """\
You are the route engine for a travel-itinerary product. Your job is to fold ONE
newly-extracted travel document (a "fragment") into an EXISTING route and return
a list of operations that update that route.

THE ROUTE MODEL
- A route is an ordered list of `stops`, each a city the traveler visits, plus a
  set of `transits` (legs) between stops.
- Every existing stop has a STABLE engine-owned id (e.g. "stop-1", "stop-2"). You
  see these ids. You MUST reference existing stops by their given id.
- You CANNOT mint, rename, renumber, or reassign ids. New stops are created ONLY
  via a `create_stop` operation; the engine assigns the fresh id.
- Cities are 3-letter uppercase codes (e.g. "ROM", "HEL").

YOUR OUTPUT
- Call the `emit_route_operations` tool exactly once with an `operations` list.
- The list may be empty if the fragment adds nothing new (e.g. a duplicate).
- Operations are applied IN ORDER. A `create_stop` must precede any operation
  that references the stop it creates — but since you do not know the id the
  engine will assign, reference a freshly-created stop's position via the
  `after` field of later `create_stop`s only; for transits/enrichment, only
  reference stops that ALREADY EXIST in the route you were given.

HARD RULES (these are non-negotiable)
1. APPEND / ENRICH, NEVER DESTROY. The route you are given is authoritative.
   Existing stops keep their identity. You never recreate, merge away, or drop an
   existing stop. You only add new stops and enrich existing ones.
2. ENRICH EXISTING STOPS BY ID. If the fragment carries timing or travelers for a
   city already present in the route, target that city's existing stop id with
   `enrich_stop` / `add_travelers` — do NOT create a second stop for it.
3. CREATE A NEW STOP ONLY FOR A GENUINELY NEW PHYSICAL STOP. If the fragment
   introduces a city not yet in the route, `create_stop` it.
4. LEGITIMATE REVISITS ARE DISTINCT STOPS. A circle/loop route can visit the same
   city twice (e.g. ROM -> HEL -> ROM). The SECOND visit to ROM is a SEPARATE
   physical stop and gets its OWN `create_stop` — do NOT merge a genuine revisit
   into the earlier same-city stop. Use timing/sequence to tell a revisit apart
   from an enrichment of the existing stop.
5. KEEP CITY ORDER CORRECT, EVEN WITH GAPS. Place new stops in their true
   chronological position relative to existing stops using the `after` field
   (an existing stop id, or null/"start" to prepend at the front). The route may
   have missing legs between known cities — that is fine; never reorder or drop
   known cities to "close" a gap.

USING THE FRAGMENT
- A transit ticket (air/bus/train) has one or more legs, each with a `from` city,
  a `to` city, departure/arrival timestamps, and travelers. For each leg:
  ensure both endpoint cities exist as stops (create the ones that are new, in
  order), enrich endpoint timing where the leg implies it, and add one
  `add_transit` between the two endpoint stops with the leg's mode, timestamps,
  and travelers.
- A hotel booking has a city, check-in/check-out timestamps, and travelers.
  Attach it to the existing stop for that city with `attach_accommodation`; if
  the city is not yet in the route, `create_stop` it first, then attach.
- `add_transit.sourceFragmentId` and the engine's bookkeeping use the fragment's
  `sourceDocumentId`. Always set `sourceFragmentId` to the fragment's
  `sourceDocumentId`.
- Carry travelers through: a stop's / transit's travelers come from the
  fragment's `travelers` list. Use `add_travelers` to union travelers onto an
  existing stop when the fragment reveals a traveler not yet recorded there.

Think about identity first: for each city the fragment mentions, decide whether it
maps to an EXISTING stop (enrich) or is a NEW physical stop (create), then emit the
minimal operation list that folds the fragment in without disturbing anything else.\
"""


# --------------------------------------------------------------------------- #
# Tool schema (derived from the op union — static, cached)
# --------------------------------------------------------------------------- #

# A list of operations, mirroring the `operations.py` Op discriminated union.
_OPERATIONS_ADAPTER: TypeAdapter[list[Op]] = TypeAdapter(list[Op])


def _operations_schema() -> dict[str, Any]:
    """JSON schema for the `operations` list, from the Pydantic op union.

    Pydantic emits ``$defs`` + a top-level ``$ref``; Bedrock/Anthropic tool
    schemas accept ``$defs``/``$ref`` so we hand the generated schema through
    directly, wrapped as the single ``operations`` property of the tool input.
    """
    return _OPERATIONS_ADAPTER.json_schema(
        by_alias=True, ref_template="#/$defs/{model}"
    )


def _tool_input_schema() -> dict[str, Any]:
    """The tool's full ``input_schema``: one required ``operations`` array."""
    ops_schema = _operations_schema()
    # `json_schema()` puts the array shape at the top level alongside `$defs`.
    # Lift `$defs` (if present) to the tool-input root so the `$ref`s resolve.
    defs = ops_schema.pop("$defs", None)
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "operations": ops_schema,
        },
        "required": ["operations"],
        "additionalProperties": False,
    }
    if defs is not None:
        schema["$defs"] = defs
    return schema


def build_tool() -> dict[str, Any]:
    """The single tool definition, with its schema cached for prompt caching.

    `cache_control` on the tool caches the (static) tool definition as part of
    the stable prefix that precedes the system prompt and messages.
    """
    return {
        "name": TOOL_NAME,
        "description": (
            "Emit the ordered list of operations that fold the new document "
            "fragment into the existing route. Reference existing stops by their "
            "engine-owned ids; create new stops only for genuinely new physical "
            "stops; never destroy or recreate existing stops."
        ),
        "input_schema": _tool_input_schema(),
        "cache_control": {"type": "ephemeral"},
    }


def build_system_blocks() -> list[dict[str, Any]]:
    """The system prompt as a single cached text block."""
    return [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def build_tool_choice() -> dict[str, Any]:
    """Force the model to call the operations tool (structured output)."""
    return {"type": "tool", "name": TOOL_NAME}
