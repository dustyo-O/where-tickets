# Technical Specification: Engine Spike — LLM-Driven Route Updater

- **Functional Specification:** `context/spec/002-engine-spike/functional-spec.md`
- **Linear:** [DUS-19](https://linear.app/dusty-work/issue/DUS-19/spike-a-llm-driven-route-updater-on-bedrock)
- **Status:** Draft
- **Author(s):** Alexander Shleyko

---

## 1. High-Level Technical Approach

A self-contained spike under `backend/spikes/route_engine_llm/`, runnable via the existing `uv` toolchain. It has three parts that share one engine entrypoint:

1. **The engine** — `update_route(current_route, fragment) -> current_route'`. Our code owns stop identity; the LLM (Claude on Bedrock) returns a list of **operations** that reference existing stops by our IDs. A deterministic applier mutates the route. Existing stops can only be *enriched*, never recreated — so the append/identity hard gate passes **by construction**.
2. **The runner** — replays each corpus scenario fragment-by-fragment through that same entrypoint, carrying the route forward, then scores the final route against `expected-route.json` and records per-step identity + ordering checks, latency, and token usage.
3. **The reporter** — aggregates a run into JSON + Markdown, and produces a cross-model comparison from multiple runs.

No Aurora, no Piccolo, no FastAPI, no SQS. Pure in-memory Python calling Bedrock. The engine is shaped like the eventual production engine so it can later be lifted into the SQS `route-update synthesis` Lambda stage.

---

## 2. Proposed Solution & Implementation Plan (The "How")

### 2.1 Module layout

| Path | Responsibility |
|------|---------------|
| `backend/spikes/route_engine_llm/__init__.py` | Package marker |
| `.../models.py` | Pydantic models: `RouteStop`, `Transit`, `Accommodation`, `WorkingRoute` (with engine-owned IDs); `Fragment` (transit/hotel union mirroring the corpus schema) |
| `.../operations.py` | Pydantic models for the LLM operation set + the deterministic `apply(route, ops) -> route` applier |
| `.../engine.py` | `update_route(route, fragment)` — builds the prompt, calls Bedrock with tool-use, validates the returned ops, applies them |
| `.../bedrock_client.py` | Thin wrapper over `AnthropicBedrock`: model selection, prompt caching, tool-use call, token-usage capture |
| `.../prompts.py` | System prompt + the operation tool schema (cached) |
| `.../corpus.py` | Load a scenario's ordered fragments + expected route from `corpus/scenarios/` |
| `.../scoring.py` | Final-route structural match; per-step identity-preservation check; per-step gap/ordering check |
| `.../pricing.py` | Dated per-model USD price table; cost from token usage |
| `.../run.py` | CLI: `python -m spikes.route_engine_llm.run --model … [--scenario/--shape/--limit]` |
| `.../report.py` | Build per-run JSON+Markdown and the cross-model comparison Markdown |
| `.../runs/` | Timestamped run artifacts (gitignored) |
| `backend/tests/spikes/test_route_engine_llm.py` | Tests for the applier, scoring, and corpus loader (no live Bedrock calls) |

### 2.2 The engine contract (operations referencing code-owned IDs)

**Working route** (our internal shape, IDs we assign):
- `stops[]`: `{ id, city, arrivalAt?, departureAt?, travelers[], accommodations[] }`
- `transits[]`: `{ id, fromStopId, toStopId, mode, departureAt, arrivalAt, travelers[], sourceFragmentId }`

`id` is a stable, monotonic engine-assigned string (e.g. `stop-1`, `stop-2`, …). The LLM sees these IDs but **cannot mint or reassign them** — new stops are created only via a `create_stop` op, and the applier assigns the next fresh ID.

**Operation set** (the tool schema bound on the Bedrock call):

| Operation | Fields | Effect |
|-----------|--------|--------|
| `create_stop` | `city`, `after?` (existing stop id or `null`/`start`) | Append a new stop with a fresh ID at the indicated position |
| `enrich_stop` | `stopId`, `arrivalAt?`, `departureAt?` | Fill timing on an **existing** stop (never overwrites a conflicting value silently — see risks) |
| `add_transit` | `fromStopId`, `toStopId`, `mode`, `departureAt`, `arrivalAt`, `travelers[]`, `sourceFragmentId` | Add a transit between two existing stops |
| `attach_accommodation` | `stopId`, `checkInAt`, `checkOutAt`, `hotelName?` | Attach a hotel stay to an existing stop |
| `add_travelers` | `stopId`, `travelers[]` | Union travelers onto a stop (multi-pax merges) |

The applier validates every referenced `stopId` exists and rejects unknown IDs. `sourceFragmentId` is set to the fragment's `sourceDocumentId`, matching the expected-route convention.

**Why this shape:** circle/loop scenarios (e.g. `ROM` twice) force the model to *choose which existing stop to enrich* vs *create a new one* — exactly the hard decision we're testing — while our code guarantees that whatever it doesn't touch keeps its identity.

### 2.3 Bedrock integration

- `AnthropicBedrock` client from `anthropic[bedrock]`. Models selected by a `--model {opus|sonnet|haiku}` alias mapped to Bedrock inference-profile IDs in config (e.g. `us.anthropic.claude-opus-4-…`), region from `AWS_REGION`.
- **Structured output** via tool-use: a single tool whose `input_schema` is the operation list; `tool_choice` forces it. Response parsed straight into the Pydantic op models.
- **Prompt caching** on the system prompt + tool schema (`cache_control`), so the static instructions are billed once per model run rather than per call.
- `temperature=0`. Retry with exponential backoff on Bedrock throttling; capture `usage` (input/output/cache tokens) per call for cost + the latency clock per call.

### 2.4 Runner & scoring

For each scenario: start with an empty route; feed fragments in the corpus-defined order; after each fragment call `update_route`; snapshot the route. Then:

- **Final-route match** (pass/fail): strip engine IDs, normalize (sort travelers, canonicalize timestamps), compare `stops` sequence and `transits` set field-by-field against `expected-route.json`.
- **Identity preservation** (hard gate): across snapshots, the set of stop IDs is **append-only** (an ID never disappears) and a given ID's `city` never changes. Any violation fails the scenario regardless of final match.
- **Gap/ordering** (hard gate): at each intermediate snapshot, the stops present must appear in an order consistent with their final positions (order-preserving subsequence by stop identity) — the engine never reorders or drops known cities to "close" a gap.

A scenario **passes** only if all three hold.

### 2.5 Report

Per run (`runs/<timestamp>-<model>/`): `results.json` (per-scenario pass/fail + failure reason category + latency + tokens + cost) and `report.md` (headline accuracy, per-axis breakdown — shape / pax / ordering / return / hotels — latency p50/p95/mean, total + per-scenario cost). Failures are **counts only**, bucketed by which check failed. A `compare.md` reads multiple run JSONs and tabulates Opus vs Sonnet vs Haiku with the go/no-go recommendation against the ≥99% bar.

### 2.6 Dependencies & tooling

- New `spike` dependency group in `backend/pyproject.toml`: `anthropic[bedrock]`. Kept out of the default runtime deps so the FastAPI image stays lean.
- A `justfile` target (e.g. `spike-engine model=…`) wrapping the runner.
- `runs/` added to `.gitignore`.

---

## 3. Impact and Risk Analysis

- **System dependencies:** none in production paths — the spike is isolated under `backend/spikes/`. It depends only on the committed corpus and Bedrock model access in the AWS account/region. **Prerequisite:** AWS credentials + Bedrock access enabled for all three Claude models in the target region.
- **Risks & mitigations:**
  - *Model returns invalid/dangling ops* → applier validates IDs and rejects; a rejected op fails the scenario (counts as model error, not a crash).
  - *Conflicting enrichment* (a fragment implies a different time than already set) → applier treats a conflicting overwrite as a failure signal rather than silently clobbering; surfaced in failure buckets.
  - *Cost/runtime of full sweeps* (192 scenarios × multi-fragment × 3 models) → prompt caching, `temperature=0`, and `--limit`/`--shape` filters for iteration; cost is measured and reported, not capped.
  - *Bedrock throttling* → bounded retry/backoff; latency still recorded per call.
  - *Non-determinism* → `temperature=0` minimizes it; runs are timestamped so re-runs are comparable rather than overwritten.
  - *Pricing drift* → price table is dated and isolated in `pricing.py`; cost is an estimate, labeled as such.
  - *Provisional route model diverging from DUS-17* → spike models are intentionally minimal and local; reconciliation with the production route model is DUS-17's job, noted in the engine ADR (DUS-21).

---

## 4. Testing Strategy

- **Unit (no live LLM):** the applier (`apply`) against hand-written op lists — create/enrich/loop cases incl. the circle double-`ROM`; the scorer (final match, identity, gap/ordering) against crafted route snapshots; the corpus loader against a fixture scenario.
- **Contract test:** a recorded/stubbed Bedrock response exercises `engine.update_route` end-to-end without network.
- **Live smoke (manual, not in CI):** run a single scenario per model to confirm Bedrock wiring, structured output, and token capture before a full sweep.
- CI runs only the offline tests (no AWS creds in CI); the full corpus sweep is an explicit local/manual command.
