"""System prompts + tool definitions for the PDF-extraction Bedrock calls.

Three Bedrock calls per document, three static prompts / tool sets:

PATH A — first Haiku-on-text call
    System: :data:`SYSTEM_PROMPT_TEXT` (allows EITHER tool).
    Tools : :data:`TOOL_EMIT_EXTRACTED_FIELDS` (structured output) AND
            :data:`TOOL_REPORT_NO_USEFUL_INFORMATION` (sentinel for non-travel
            text — routes to the vision path).

PATH B — vision-leg Haiku call (after Sonnet OCRs the page images)
    System: :data:`SYSTEM_PROMPT_TEXT`.
    Tools : ONLY :data:`TOOL_EMIT_EXTRACTED_FIELDS` — the sentinel is gone at
            this point (per tech-spec §2.2): if Sonnet read pixels and Haiku
            still can't make sense of them, the document genuinely failed,
            and the orchestrator surfaces that as ``ExtractionFailedError``
            rather than looping forever.

PATH C — Sonnet vision (Sonnet on JPEGs of the page set)
    System: :data:`SYSTEM_PROMPT_VISION` — Sonnet OCRs the images and returns
            plain text. NOT a tool-use call.

Both system prompts and both tool definitions are static module-level constants
designed to live in the prompt-cache prefix (``cache_control: ephemeral`` is
applied at the tool / system layer; see the spike's ``build_tool`` /
``build_system_blocks`` for the same idiom). They never mutate at runtime.

The :data:`TOOL_EMIT_EXTRACTED_FIELDS` ``input_schema`` is derived directly
from :data:`where_tickets.extraction.schema.EXTRACTOR_SCHEMA` — there is
exactly one source of truth (``corpus/pdf/schema/expected-fields.schema.json``)
for both the model-side contract (``input_schema``) and the post-call
validator (:func:`where_tickets.extraction.schema.validate`).
"""

from __future__ import annotations

from typing import Any, Final

from where_tickets.extraction.schema import EXTRACTOR_SCHEMA

__all__ = [
    "SYSTEM_PROMPT_TEXT",
    "SYSTEM_PROMPT_VISION",
    "TOOL_EMIT_EXTRACTED_FIELDS",
    "TOOL_EMIT_EXTRACTED_FIELDS_NAME",
    "TOOL_REPORT_NO_USEFUL_INFORMATION",
    "TOOL_REPORT_NO_USEFUL_INFORMATION_NAME",
    "VISION_USER_PROMPT",
]


# --------------------------------------------------------------------------- #
# Tool names (used by the orchestrator to dispatch on `tool_use.name`)
# --------------------------------------------------------------------------- #

TOOL_EMIT_EXTRACTED_FIELDS_NAME: Final[str] = "emit_extracted_fields"
TOOL_REPORT_NO_USEFUL_INFORMATION_NAME: Final[str] = "report_no_useful_information"


# --------------------------------------------------------------------------- #
# Tool definitions (static, marked for prompt caching)
# --------------------------------------------------------------------------- #

# The structured-extraction tool. `input_schema` IS the corpus schema (minus the
# corpus-only metadata fields, which the extractor never knows about). Anthropic
# enforces this schema at the model level, so a malformed payload becomes a
# parser-side failure we never see in practice.
TOOL_EMIT_EXTRACTED_FIELDS: Final[dict[str, Any]] = {
    "name": TOOL_EMIT_EXTRACTED_FIELDS_NAME,
    "description": (
        "Emit every fact printed on this travel document as a single structured "
        "payload. Use this whenever the input is a recognisable travel document "
        "(air, rail, bus ticket; hotel or Airbnb booking; or a supplementary "
        "travel document such as a parking pass or sightseeing voucher). Values "
        "must reflect the document's literal printed form (city names as printed, "
        "ISO local datetimes as printed, etc.). Leave arrays empty / sub-fields "
        "omitted when a fact is not present on the document — never guess."
    ),
    "input_schema": EXTRACTOR_SCHEMA,
    "cache_control": {"type": "ephemeral"},
}


# The sentinel tool used only on the FIRST Haiku-on-text call (PATH A). If the
# text layer is not a travel document at all (a stray web page, an error page,
# blank-ish text, a generic confirmation email), Haiku reports that here
# instead of hallucinating an `emit_extracted_fields` payload, and the
# orchestrator routes the PDF through the vision path (PATH B) for a second
# look at the rendered pixels.
TOOL_REPORT_NO_USEFUL_INFORMATION: Final[dict[str, Any]] = {
    "name": TOOL_REPORT_NO_USEFUL_INFORMATION_NAME,
    "description": (
        "Call this when the document text contains no useful travel information "
        "(e.g. it's a generic web page, an error message, a near-blank page, or "
        "otherwise unrelated to travel). Provide a short reason. The orchestrator "
        "treats this as a signal to re-read the document via the vision path."
    ),
    "input_schema": {
        "type": "object",
        "required": ["reason"],
        "additionalProperties": False,
        "properties": {
            "reason": {
                "type": "string",
                "description": (
                    "One short sentence explaining why no useful travel "
                    "information could be extracted from the text."
                ),
                "minLength": 1,
            }
        },
    },
    "cache_control": {"type": "ephemeral"},
}


# --------------------------------------------------------------------------- #
# System prompts (static, designed for prompt caching)
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT_TEXT: Final[str] = """\
You extract structured travel-document data from text.

The input is the text layer of a single PDF — usually a printed travel
document, occasionally something unrelated. Your job is to read it carefully
and decide between two outcomes:

1. The document IS a travel document. Call the `emit_extracted_fields` tool
   exactly once with every printed fact captured in the structured payload.
2. The text carries NO useful travel information (a generic web page, an error
   message, a near-blank page, an unrelated confirmation email, illegible
   garbage). Call the `report_no_useful_information` tool instead with a one-
   sentence reason. Do NOT fabricate an `emit_extracted_fields` payload to
   "fill in the blanks".

Recognised document types (`document_type`):
- `air_ticket`       — airline boarding pass / e-ticket
- `rail_ticket`      — train ticket / e-ticket
- `bus_ticket`       — coach / bus ticket
- `hotel_booking`    — hotel reservation confirmation
- `airbnb_booking`   — Airbnb (or comparable short-term rental) confirmation
- `supplementary`    — parking pass, sightseeing voucher, transfer voucher,
                       luggage tag, or any other travel-adjacent document
                       that doesn't fit the five categories above

Extraction rules — read carefully:

A. LITERAL PRINTED FORM. Values reflect what is printed on the document.
   - City names use the printed form (e.g. "Paris", "Lisbon") — do NOT
     normalise to IATA codes or any other canonical representation.
   - Datetimes use ISO local form `YYYY-MM-DDTHH:MM:SS` (no timezone,
     no offsets) — even if the document prints them differently, you
     re-format them into ISO local, preserving the printed local clock
     time exactly. Do not convert to UTC; do not infer a missing time.
   - Currency codes are 3-letter uppercase ISO 4217 (e.g. "EUR", "USD").
   - Identifiers: IATA codes for airports (e.g. "CDG"), printed station /
     terminal / property / venue names otherwise.
   - Traveler names use the printed spelling, including any honorifics or
     middle initials, exactly as they appear.

B. NO GUESSING. If a fact isn't on the document, leave it out.
   - Optional sub-fields (e.g. a venue's `valid_from_datetime`) are simply
     omitted from that item.
   - Optional arrays (`prices`, `accommodations`, `venues`, `stations`)
     can be empty when the document doesn't carry that bucket.
   - Required arrays (`cities`, `travelers`) must contain at least one entry
     — if the document genuinely lacks travellers or cities, that's a
     `report_no_useful_information` case, not an empty array.

C. ONE PAYLOAD PER DOCUMENT. A multi-leg ticket is still one document and
   one `emit_extracted_fields` call; list every endpoint city in `cities`
   and every transit endpoint in `stations`, in the order they're printed.

D. `pdf_kind` records HOW the document was read: `text` when the text layer
   carries the facts (this call's case for the first Haiku-on-text pass) or
   `rasterized` when the facts come from OCR of the page images (the vision-
   leg Haiku call). The orchestrator tells you which by which path you're
   on; default to `text` if unsure.

E. QR / BARCODE PAYLOADS — DO NOT EXTRACT. Always return `qr_codes: []`.
   QR and barcode payloads are encoded in image regions, not in the text
   layer; any nearby text label that happens to mirror the payload is a
   corpus-authoring artefact, not a reliable signal. Reading the actual
   barcode image is a separate concern, tracked in DUS-33; this extractor
   leaves `qr_codes` empty regardless of what the text appears to say.

The structured payload's schema is enforced by the tool's `input_schema`.
Read it carefully; every required field must be present.\
"""


# The user-message text that accompanies the page-image blocks in the Sonnet
# vision (PATH B) call. The image blocks come first in the user message, and
# this short orientation string follows them — it just points Sonnet back at
# the strict OCR rules in :data:`SYSTEM_PROMPT_VISION`. Kept terse and static
# (no per-document interpolation) so it lives in the prompt-cache prefix and
# never invalidates the cache.
VISION_USER_PROMPT: Final[str] = (
    "Transcribe the printed text on the pages above into plain text, "
    "following the OCR rules in the system prompt."
)


SYSTEM_PROMPT_VISION: Final[str] = """\
You OCR a travel document image (or a sequence of page images that together
make up one document) into plain text.

Read every page in reading order. Return ONLY the readable text on the
page(s), preserving the document's line breaks, paragraph breaks, and the
natural reading order (top-to-bottom, left-to-right; respect column layout).

Strict rules:

- Output is PLAIN TEXT. No commentary, no preamble ("Here is the text..."),
  no markdown formatting, no JSON, no schema, no field labels you invent.
- Preserve numbers, codes, datetimes, city names, traveler names, and
  prices EXACTLY as printed. Do not normalise, translate, or expand
  abbreviations. Do not convert timezones. Do not infer missing characters.
- IGNORE QR codes and barcodes entirely. Do not transcribe them, do not
  describe them, do not include any nearby text label that mirrors a QR
  payload. Barcode decoding is a separate concern (tracked in DUS-33);
  this OCR pass treats QR / barcode regions as if they weren't there.
- If part of the page is illegible, write `[illegible]` in place of the
  unreadable text rather than guessing.
- If the page is genuinely blank or carries no travel-document text, return
  an empty string. Do not narrate the absence.

Your output feeds a downstream model that extracts structured fields from
this plain text, so legibility and fidelity to the printed text matter more
than visual layout fidelity.\
"""
