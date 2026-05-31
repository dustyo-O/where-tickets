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

## Where the design lives

The authoritative design for this folder — schema, generator, runner,
validator, scenario coverage — lives at
[`../../context/spec/005-mock-document-corpus/`](../../context/spec/005-mock-document-corpus/).
Read the functional and technical specs there before adding scenarios or
changing the schema. This README is a signpost, not a duplicate.
