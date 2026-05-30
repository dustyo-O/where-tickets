# ADR: Route Engine — Algorithmic for v1

- **Linear:** [DUS-21](https://linear.app/dusty-work/issue/DUS-21/engine-decision-write-the-adr)
- **Status:** Accepted
- **Date:** 2026-05-30
- **Decides between:** [DUS-19 (LLM spike)](https://linear.app/dusty-work/issue/DUS-19/spike-a-llm-driven-route-updater-on-bedrock) and [DUS-20 (algorithmic spike)](https://linear.app/dusty-work/issue/DUS-20/spike-b-algorithmic-graph-route-builder)
- **Author:** Alexander Shleyko

---

## Context

The route engine is the heart of the product — it turns a pile of arbitrarily-ordered, multi-source travel documents (air/rail/bus tickets, hotel bookings) into one coherent, ordered sequence of cities, preserving identity across incremental updates and tolerating gaps. Before building the Trip Route View on top of it, we needed to commit to **one** approach for v1.

The roadmap (Phase 1 → Route Engine (Foundation) → Engine Spike & Decision) called for prototyping both an LLM-driven and an algorithmic approach against the same 192-scenario corpus, then committing to one for v1 with evidence. The product success metric requires **≥99% route accuracy**.

Both spikes shipped the same eval harness (corpus loader, hard-gated scoring, results.json schema, runner, cross-engine comparison report) so the comparison is apples-to-apples. The operation contract and identity-preservation guarantees are identical: the model/engine emits an op list against an engine-owned route, our shared applier mutates it in place.

## Evidence

Full 192-scenario corpus, identical hard gates (final-route match + append/identity preservation + gap/ordering), iterated prompts (LLM) and iterated rules (algorithmic). Final numbers:

| model | accuracy | passed/total | total cost | latency mean | clears ≥99% |
|---|---:|---:|---:|---:|:-:|
| Haiku 4.5 | 27.1% | 52/192 | $2.31 | 1.95s | ❌ |
| Sonnet 4.6 | 79.2% | 152/192 | $7.07 | 3.18s | ❌ |
| Opus 4.6 | 83.9% | 161/192 | $10.71 | 4.34s | ❌ |
| **algorithmic** | **100.0%** | **192/192** | **$0.00** | **<1 ms** | **✅** |

Per-shape — where the LLM hit ceilings:

| shape | Haiku | Sonnet | Opus 4.6 | **algorithmic** |
|---|---:|---:|---:|---:|
| straight | 60.9% | 95.3% | 100% | **100%** |
| circle | 12.5% | 92.2% | 92.2% | **100%** |
| star | 7.8% | 50.0% | 59.4% | **100%** |

Per-ordering (fragment arrival order):

| ordering | Haiku | Sonnet | Opus 4.6 | **algorithmic** |
|---|---:|---:|---:|---:|
| forward | 33.3% | 91.7% | 100% | **100%** |
| reverse | 18.8% | 70.8% | 72.9% | **100%** |
| bisect | 29.2% | 77.1% | 72.9% | **100%** |
| seeded-shuffle | 27.1% | 77.1% | 89.6% | **100%** |

Failure buckets:

| bucket | Haiku | Sonnet | Opus | algorithmic |
|---|---:|---:|---:|---:|
| `engine_error` | 6 | 7 | 0 | **0** |
| `final_mismatch` | 134 | 33 | 31 | **0** |

Determinism: two back-to-back `just spike-engine-algo` runs produced byte-identical `results.json` modulo the run timestamp and per-scenario latency fields. The algorithmic engine has no randomness and no wall-clock-dependent decisions.

Total spike spend: **~$34** on the LLM side; **$0** on algorithmic. Cross-engine comparison artifact: `backend/spikes/route_engine_llm/runs/compare-20260529T213439Z.md` (local; `runs/` is gitignored).

## Decision

**Adopt the algorithmic engine as the route engine for v1.**

- It is the only candidate that clears the ≥99% accuracy bar (192/192 = 100%).
- It dominates every per-axis breakdown — including the LLM's hardest spots (stars: algorithmic 100% vs Opus 59.4%; reverse ordering: algorithmic 100% vs Opus 72.9%).
- Inference cost is $0 vs ~$10.71/sweep for Opus; per-call latency is sub-millisecond vs multi-second.
- Deterministic by construction, so production behavior is reproducible and bug-triageable.
- Zero `engine_error` failures across the full corpus — the operation contract is sound under the rules engine.

## Consequences

**What lands as v1's engine.** The code under `backend/spikes/route_engine_algorithmic/` (Pydantic models, op set + applier, per-traveler-per-slot identity classifier, per-batch ledger, hotel handling, runner, eval harness) graduates from "spike" to the seed of the production `route-update synthesis` Lambda stage. The contract (`update_route(route, fragment) -> UpdateResult`) is already production-shape; the applier already owns identity by construction.

**What we drop from the LLM spike.** Nothing is force-deleted — the LLM spike stays in tree under `backend/spikes/route_engine_llm/` as the historical reference and regression baseline. The next time this code is touched is at engine-promotion time: the shared types (`models`, `operations`, `corpus`, `scoring`, `report`, `compare`) move out of `route_engine_llm/` into a common module (the TODO comments on the sibling imports name this), and the algorithmic engine moves to its production location. The LLM spike then either stays archived for reference or is removed once the comparison is no longer historically interesting.

**What this unblocks.** The Trip Route View (DUS-7 area of Phase 1) can now build directly on the algorithmic engine's data shape. Phase-2 work (custom legs, completeness checks) integrates by emitting additional op types or fragment shapes through the same applier; the per-traveler-per-slot classifier already accommodates accommodation-only stops.

**Cost & operational implications.** No Bedrock/Anthropic dependency in the route-engine code path. No per-document inference cost. No model-access gating, no rate-limit/throttling failure modes, no prompt-caching tuning to maintain. CI runs the engine on the full corpus at zero cost (offline, sub-second).

## What we keep from the LLM spike

The single most valuable artifact from DUS-19 is the **per-traveler-per-slot identity framing** in the system prompt — the three explicit create-new conditions ((a) city not in route, (b) chronologically-disjoint with intervening different-city stop, (c) per-traveler slot already filled) plus the arrival-after-departure sanity check. That framing is what cracked Sonnet from 54.7% → 79.2% on the LLM side, and it is exactly what the algorithmic engine's `classify_event` translates into code. Without the LLM spike's prompt iteration, the algorithmic rules would have had to be discovered independently.

The eval harness (corpus loader, hard-gated scoring, results.json schema, cross-engine compare) is also reused verbatim by the algorithmic spike — the rapid Slice 2/3/4 iteration was only possible because the harness already existed and was trusted.

## Future / open options

**LLM as a fallback/reviewer (out of scope here, viable later).** Real-world routes in production may include shapes outside the generator's coverage (unusual loops, weird timings, mixed-mode legs the corpus doesn't exercise, low-confidence OCR extraction artifacts). If the algorithmic engine ever stumbles on production traffic, the LLM remains as a fallback or reviewer layer — Opus already handles 83.9% of cases unaided, so a "rules first, LLM if rules error or low-confidence" hybrid would be straightforward to wire in via the same op contract. Not built now; flagged as a deliberate future option.

**Corpus extension.** The current 192-scenario corpus is generator-produced (3 shapes × 4 pax × 4 orderings × return × hotels). Both spikes were scored against the same generated set, so the algorithmic engine's 100% is *on this corpus* — not a universal guarantee. Phase-2 work (custom legs, real-world ticket diversity) should feed back into the corpus, and any regression in algorithmic accuracy on extended corpora is a signal to revisit the hybrid option.

**Promotion / refactor.** The algorithmic engine still lives under `backend/spikes/`. Promoting it requires (1) extracting the shared types from `route_engine_llm/` to a common module, (2) wiring the engine into the SQS pipeline's route-update-synthesis stage, (3) replacing the spike's in-memory `WorkingRoute` with the production route data model (DUS-17 — already specced, separate ticket). None of that is in scope for this ADR; it's the next slice of engineering work after the DUS-21 decision lands.
