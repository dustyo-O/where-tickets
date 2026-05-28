# Tasks: Engine Spike — LLM-Driven Route Updater

> Each slice is end-to-end runnable. Don't move on until the slice's verification passes.

---

- [x] **Slice 1: Route model + deterministic operation applier (offline)**
  - [x] Create `backend/spikes/route_engine_llm/` package; add `models.py` with `Accommodation`, `RouteStop`, `Transit`, `WorkingRoute` (engine-owned monotonic `stop-N` IDs) and the `Fragment` transit/hotel union mirroring `corpus/schema/extracted-fragment.schema.json`. **[Agent: python-backend]**
  - [x] Add `operations.py`: Pydantic op models (`create_stop`, `enrich_stop`, `add_transit`, `attach_accommodation`, `add_travelers`) and the deterministic `apply(route, ops) -> route` applier that mints fresh IDs, validates referenced `stopId`s, and rejects dangling/conflicting ops. **[Agent: python-backend]**
  - [x] Add `backend/tests/spikes/test_applier.py`: create/enrich/transit/accommodation cases plus the **circle double-`ROM`** case (enrich the right stop, never merge the two) and a dangling-ID rejection case. **[Agent: python-backend]**
  - [x] **Verify:** `cd backend && uv run pytest tests/spikes/test_applier.py` passes; applier never reassigns an existing ID. **[Agent: python-backend]**

- [x] **Slice 2: Corpus loader + scoring (offline)**
  - [x] Add `corpus.py`: load a scenario's ordered `fragments/*.json` + `expected-route.json` from `corpus/scenarios/`, parsing into the Slice 1 models. **[Agent: python-backend]**
  - [x] Add `scoring.py`: (a) **final-route match** — strip engine IDs, normalize, compare `stops` sequence + `transits` set field-by-field vs expected; (b) **identity preservation** — stop-ID set append-only, city stable per ID across snapshots; (c) **gap/ordering** — partial stops are an order-preserving subsequence of final positions by identity. A scenario passes only if all three hold. **[Agent: python-backend]**
  - [x] Add `backend/tests/spikes/test_scoring.py`: feed an expected route back through the matcher (self-match = pass); crafted snapshot sequences that trip each of the three checks. **[Agent: python-backend]**
  - [x] **Verify:** `uv run pytest tests/spikes/test_scoring.py` passes; a deliberately reordered/identity-broken snapshot fails the right check. **[Agent: python-backend]**

- [x] **Slice 3: Engine + Bedrock wiring, proven offline (stubbed)**
  - [x] Add `anthropic[bedrock]` as a new `spike` dependency group in `backend/pyproject.toml`; add `backend/spikes/route_engine_llm/runs/` to `.gitignore`. **[Agent: python-backend]**
  - [x] Add `prompts.py` (system prompt + single operation-list tool schema, marked for prompt caching) and `bedrock_client.py` (thin `AnthropicBedrock` wrapper: model alias→inference-profile-ID map, `tool_choice` forced, `temperature=0`, token-usage + per-call latency capture, bounded retry/backoff). **[Agent: bedrock-llm]**
  - [x] Add `engine.py`: `update_route(route, fragment)` — render prompt, call client, parse tool-use into op models, `apply`. **[Agent: bedrock-llm]**
  - [x] Add `backend/tests/spikes/test_engine_contract.py`: drive `update_route` end-to-end against a **recorded/stubbed** Bedrock response (no network) and assert the route mutates correctly. **[Agent: bedrock-llm]**
  - [x] **Verify:** `uv run pytest tests/spikes/` passes with no AWS credentials present (CI-safe). **[Agent: python-backend]**

- [x] **Slice 4: Runner + per-run report, live on a subset**
  - [x] Add `pricing.py` (dated per-model USD table; cost from token usage) and `report.py` (per-run `results.json` + `report.md`: accuracy, per-axis breakdown, latency p50/p95/mean, cost; failures bucketed by which check failed — counts only). **[Agent: bedrock-llm]**
  - [x] Add `run.py` CLI (`python -m spikes.route_engine_llm.run --model {opus|sonnet|haiku} [--scenario/--shape/--limit]`): empty route → feed fragments in corpus order → `update_route` per fragment → snapshot → score; write to `runs/<timestamp>-<model>/`. Add a `just spike-engine` recipe wrapping it. **[Agent: bedrock-llm]**
  - [x] **Verify (live, needs AWS):** `just spike-engine haiku --limit 3` completed against real Bedrock (eu-north-1, eu.* profiles), wrote `results.json` + `report.md` with real token/cost/latency (1/3 smoke pass, ~$0.019). **[Agent: bedrock-llm]**

- [x] **Slice 5: Full sweep + cross-model comparison & recommendation**
  - [x] Add `compare.py` with `build_compare_md` + `python -m …compare` CLI + `just spike-compare` recipe: read N run JSONs and tabulate models (accuracy, per-axis, latency, cost) with a go/no-go recommendation against the ≥99% bar. 14 offline tests. **[Agent: bedrock-llm]**
  - [x] Run the full 192-scenario corpus for **Haiku, Sonnet, and Opus 4.6** (Opus 4.7 not enabled in the target account; pricing identical to 4.6). Two prompt-iteration commits in between (per-traveler-per-slot identity + symmetric chronological "between" + arr-after-dep sanity). Final: Haiku 27.1%, Sonnet 79.2%, Opus 4.6 83.9%. Total spike spend ~$34. **[Agent: bedrock-llm]**
  - [x] **Verify (live):** three full run dirs + `compare.md` (`runs/compare-20260528T135228Z.md`) render Haiku/Sonnet/Opus side-by-side; **no model clears ≥99%**; tool's recommendation: fall back to the algorithmic engine spike (DUS-20). Wall is star routes (Opus 59.4%) and reverse fragment ordering. **[Agent: bedrock-llm]**

---

## Prerequisites

| Task/Slice | Issue | Recommendation |
| --- | --- | --- |
| Slices 4 & 5 verification | Live Bedrock calls need AWS credentials with **Bedrock model access enabled for Opus, Sonnet, and Haiku** in your `AWS_REGION` | Confirm creds + model access before running; Slices 1–3 need nothing and run in CI |
