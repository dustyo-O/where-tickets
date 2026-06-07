# PDF extraction

LLM pipeline that turns a single travel-document PDF into a structured
`ExtractedFields` payload. Three Bedrock call paths (per spec
`006-ai-document-understanding-pdf-extraction`):

- **Path A — Haiku-on-text.** First pass: PyMuPDF text layer → Claude Haiku
  4.5 with two tools (`emit_extracted_fields`, `report_no_useful_information`).
- **Path B — Sonnet vision + Haiku-text.** Empty text or sentinel → render
  pages to JPEG, Sonnet 4.6 OCRs them, Haiku extracts from the OCR string.
- **Path C — Sonnet-on-text fallback.** Haiku-on-text returned a non-sentinel
  response that failed the schema → re-issue the same text to Sonnet with only
  `emit_extracted_fields` exposed.

Source of truth for the structured payload schema is
`corpus/pdf/schema/expected-fields.schema.json` (re-exported as
`EXTRACTOR_SCHEMA` in `schema.py` and stamped onto the tool's `input_schema`).

## Setup

The extractor calls Bedrock via the cross-region Anthropic inference profiles
in `eu-north-1`. Credentials come from the `wt-bedrock` SSO profile.

```sh
aws sso login --profile wt-bedrock
export AWS_PROFILE=wt-bedrock
export AWS_REGION=eu-north-1
```

Optional model-id overrides (defaults track the current Claude generation):

```sh
# Defaults — only set these if you want to point at a different profile.
export WT_BEDROCK_MODEL_HAIKU=eu.anthropic.claude-haiku-4-5-20251001-v1:0
export WT_BEDROCK_MODEL_SONNET=eu.anthropic.claude-sonnet-4-6
```

The optional `extraction` dep group carries `anthropic[bedrock]`, `pymupdf`,
and `jsonschema`. The Justfile recipes below install it into an isolated venv
via `uv run --isolated --group extraction` so the persistent backend venv
stays lean (and `just lint` stays green).

## Usage

Debug one PDF:

```sh
AWS_PROFILE=wt-bedrock just extract-pdf /abs/path/to/document.pdf
```

Stdout is the extracted payload (pipeable to `jq`); stderr carries the
`extraction_path` / `model_path` diagnostic banner.

Full Layer 1 sweep with PASS/FAIL:

```sh
AWS_PROFILE=wt-bedrock just test-pdf-corpus
```

## Observed cost + latency (Slice 9 final sweep, 2026-06-07)

Layer 1 sweep on 150 scenarios — 149 PASS (99.3%), path-mix `text=128 vision=22`.

Per-scenario latency (sampled across 20 mixed-type scenarios from the final
sweep, single sequential run, eu-north-1):

| path                       | n  | mean latency | p50    | p95     |
|----------------------------|----|--------------|--------|---------|
| haiku-text (Path A)        | 17 | ~2.9 s       | ~2.5 s | ~3.6 s  |
| sonnet-vision + haiku-text | 3  | ~12.5 s      | ~12.6 s| ~12.6 s |

Token totals per scenario (mean across the same sample):

| path                       | input tokens | output tokens |
|----------------------------|--------------|---------------|
| haiku-text (Path A)        | ~4,600       | ~355          |
| haiku-text leg in Path B   | ~4,400       | ~355          |

**Instrumentation caveat.** The `extract_pdf` structured log line currently
sums `tokens_*` across `usages` it received, which on the vision path
excludes the Sonnet OCR leg's own token usage (`complete_vision` returns the
OCR text but not its `Usage` — see `extract.py:321`). The numbers above
therefore *understate* Path B token cost. A follow-up should plumb the
vision-OCR `Usage` through to the log line.

**Cost estimate for one Layer 1 sweep (150 PDFs).** Using approximate
eu-north-1 inference-profile pricing —

- Haiku 4.5: ~$0.001 / 1k input, ~$0.005 / 1k output
- Sonnet 4.6: ~$3 / M input, ~$15 / M output

A pure-Haiku scenario is ~$0.006 (Path A). A Path-B scenario is ~$0.02 with
the upper bound (treating the haiku leg as Sonnet-priced as a stand-in for
the missing Sonnet OCR tokens). Projected sweep total: **~$1.20-$1.50**.

Prompt caching is wired (`cache_control: ephemeral` on system + tools), but
the runner makes one call per PDF with no warmup, so cache_read tokens stayed
at 0 across the sample. The structured log line carries
`tokens_cache_read` / `tokens_cache_creation` so a future warm-cache sweep
will be measurable.

## Known limitations

- **Scenario 077-hotel-2nt-1pax-frankfurt** — the corpus expects
  `accommodations[0].identifier == "Solana Frankfurt Bankenviertel"`. The
  PDF's `PROPERTY` block prints the name on two lines
  (`Solana Frankfurt\nBankenviertel`); Haiku reads the first line only and
  emits `"Solana Frankfurt"`. The same neighborhood-suffix pattern appears
  in scenarios 075, 079, 090, 092, 097, 098 and all pass — this is a layout-
  specific line-wrap edge case, not a systemic prompt issue, so it lands as a
  known-limitation rather than a rule-C extension. A follow-up could either
  re-template that hotel's PROPERTY block in the corpus generator or teach
  the prompt to re-join adjacent printed lines under a label.

## Slice progression

Design docs live in
`context/spec/006-ai-document-understanding-pdf-extraction/`; this README
captures the slice 9 acceptance state (Layer 1 ≥ 99%, prompt rule C extended
for return tickets). See `prompts.py` rule C for the return-ticket convention
and `extract.py` for the path A/B/C orchestration.
