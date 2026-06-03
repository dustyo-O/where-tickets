# Mock-Document Corpus (PDF)

A curated set of PDF tickets and bookings for stress-testing the **PDF
extraction** step of the pipeline. This is the input-side counterpart to the
sibling fragment corpus at [`../scenarios/`](../scenarios/) — different
artifact, different layer.

## Local-dev setup

The Layer 1 generator uses WeasyPrint (Slice 3+) to render HTML/CSS templates
to PDF. WeasyPrint pulls in native dependencies (Pango / Cairo) that must be
installed at the system level:

- **macOS**: `brew install pango` (pulls in Cairo and the other transitive
  native libs).
- **Linux (Debian / Ubuntu)**: `apt-get install libpango-1.0-0 libpangoft2-1.0-0`
  (or the `pango1.0-tools` + `libcairo2` package set, depending on distro).

The Slice 1 fixture toolchain (`fpdf2`) needs **no** system dependencies and
works out of the box once the `corpus` dep group is installed
(`cd backend && uv sync --group corpus`).

- **This corpus** (`corpus/pdf/`) tests **PDF -> extracted fields**: given a
  realistic-looking ticket PDF, does the extractor produce the right cities,
  dates, travelers, prices, and QR codes?
- **The sibling corpus** (`corpus/scenarios/`) tests **fragments -> route**:
  given already-extracted document fragments, does the route engine assemble
  them into the correct trip? It is intentionally untouched by this work.

Together, a green PDF corpus and a green fragment corpus give end-to-end
confidence in the pipeline without either corpus having to know about the
other.

## Layers

- **Layer 1 — generated fake PDFs** (committed): deterministic-data,
  randomized-noise look-alike tickets that prove extraction works on
  controlled inputs.
- **Layer 2 — real PDFs** (local-only, gitignored): actual tickets collected
  from real trips that prove extraction works on documents nobody on the team
  would invent. Real PDFs carry personal data and must never land in git
  history.

## Layer 2 — Real PDFs (local-only)

Layer 2 is opt-in. Drop a real PDF + sibling `expected-fields.json` into a
trip directory and the next `just test-pdf-corpus` run will discover it:

```
corpus/pdf/layer2/
└── <trip-slug>/                       # e.g. porto-trip
    ├── 01-<doc>.pdf
    ├── 01-<doc>.expected-fields.json
    ├── 02-<doc>.pdf
    ├── 02-<doc>.expected-fields.json
    └── README.md                      # optional contributor note; ignored
```

Trip grouping is purely organizational. Every PDF is its own scenario at
runtime; an `<NN>-<doc>.pdf` without a sibling `<NN>-<doc>.expected-fields.json`
is reported as a discovery failure (no silent skips).

The hand-authored JSON must satisfy the same rules as Layer 1:

- conforms to [`schema/expected-fields.schema.json`](./schema/expected-fields.schema.json) (Draft 2020-12)
- the city-integrity rule (every `stations[]/accommodations[]/venues[].city`
  must appear in top-level `cities[]`)
- per-`document_type` min counts (air/rail/bus → `stations[] >= 2`;
  hotel/airbnb → `accommodations[] >= 1`)
- transit-ticket stations carry `departure_datetime` or `arrival_datetime`.

**Determining `pdf_kind`:** open the PDF in any viewer. If you can select
text with the cursor, set `pdf_kind: "text"`. Otherwise (scanned, exported
as image, etc.) set `pdf_kind: "rasterized"`.

**Never commit Layer 2 PDFs.** They carry personal data. The repo's
`.gitignore` covers `corpus/pdf/layer2/**` except for `.gitkeep`, and
`corpus/pdf/validate.py` runs a `layer2-leak` guard that fails CI if anything
else under `corpus/pdf/layer2/` ends up tracked. If you accidentally
`git add` a real PDF, drop it from the index with:

```bash
git rm --cached corpus/pdf/layer2/<trip>/<file>.pdf
```

Minimal valid `expected-fields.json` example:

```json
{
  "scenario_id": "porto-trip/01-hotel-booking",
  "document_type": "hotel_booking",
  "pdf_kind": "text",
  "cities": ["Porto"],
  "stations": [],
  "accommodations": [
    {
      "city": "Porto",
      "kind": "hotel",
      "identifier": "Hotel Ribeira",
      "check_in_datetime": "2027-04-15T15:00:00",
      "check_out_datetime": "2027-04-18T11:00:00"
    }
  ],
  "venues": [],
  "travelers": ["Alice Example"],
  "prices": [{"amount": 320.0, "currency": "EUR"}],
  "qr_codes": []
}
```

## Where the design lives

The authoritative design for this folder — schema, generator, runner,
validator, scenario coverage — lives at
[`../../context/spec/005-mock-document-corpus/`](../../context/spec/005-mock-document-corpus/).
Read the functional and technical specs there before adding scenarios or
changing the schema. This README is a signpost, not a duplicate.
