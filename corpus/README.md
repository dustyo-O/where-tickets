# Corpus

A deterministically generated set of scenarios for stress-testing the future
**route-assembly engine**.

> The corpus tests **route assembly from pre-structured fragments**, NOT PDF
> extraction. PDF extraction is considered tractable; the hard problem is
> composing one coherent travel route from a pile of fragments (tickets, hotel
> bookings) that arrive in arbitrary order, span multiple travelers, and mix
> transport modes.

The authoritative design for this folder lives in
[`context/spec/001-project-bootstrap/technical-considerations.md`](../context/spec/001-project-bootstrap/technical-considerations.md)
§2.8.

## Layout

```
corpus/
├── README.md
├── schema/
│   ├── extracted-fragment.schema.json   # one fragment = one simulated document
│   └── expected-route.schema.json       # the composed answer
├── generator/                           # deterministic Python generator
│   ├── __main__.py                      # `python -m corpus.generator`
│   ├── matrix.py                        # enumerates the coverage matrix
│   ├── shapes.py                        # straight / circle / star generators
│   ├── fragmenter.py                    # composed route → per-document fragments
│   ├── orderings.py                     # forward / reverse / bisect / seeded-shuffle
│   ├── cities.py                        # deterministic city pool
│   └── scenario.py                      # glue: spec → fragments + expected-route
├── validate.py                          # schema validation + drift check
└── scenarios/                           # COMMITTED, generated — do not hand-edit
    └── NNN-<shape>-<pax>p-<order>[-return][-hotels]/
        ├── fragments/01-<doc-type>.json, 02-..., ...
        ├── expected-route.json
        └── README.md                    # one-line scenario summary
```

PDFs are **not** in scope for this slice — `source-pdfs/` is reserved for the
later Document Ingest umbrella.

## Coverage matrix

The generator enumerates the cartesian product of these axes:

| Axis            | Values                                            |
|-----------------|---------------------------------------------------|
| Travelers       | `1`, `2`, `3`, `4`                                |
| Route shape     | `straight`, `circle`, `star`                      |
| Return          | `no`, `yes` (final hop lands back at origin)      |
| Hotels          | `no`, `yes` (one hotel-booking per stopover)      |
| Fragment order  | `forward`, `reverse`, `bisect`, `seeded-shuffle`  |
| Leg count       | shape-appropriate (e.g., straight 2–4, circle 4–5, star 4–6) |
| Primary mode    | rotates across `air`, `bus`, `train`              |

Total: `3 * 4 * 2 * 2 * 4 = 192` scenarios. Every value of every axis appears
many times, and every ordering appears with every shape × pax count.

### Sampling rule

The generator does **not** explode the full cartesian of every axis (leg count
and mode would push the total into the thousands). Instead, leg count and
primary mode are picked from the scenario index, so they vary across scenarios
without inflating the total. The hard axis — fragment ordering — is densely
covered with every shape and traveler count.

## Determinism

- Fixed epoch: all timestamps anchored at `2027-03-01T00:00:00Z`.
- Seeds: every random choice goes through `random.Random(seed)` where the seed
  is a stable SHA-256 hash of the scenario's axis values.
- No `datetime.now()`, no unseeded `random`, no dict-iteration assumptions:
  all JSON is dumped with `indent=2, sort_keys=True`.
- Re-running the generator produces byte-identical files. CI fails on drift.

## How to regenerate

The generator is pure-stdlib Python 3.12+. With `uv` available:

```bash
uv run --python 3.12 python -m corpus.generator
# or
just regen-corpus
```

Output goes to `corpus/scenarios/` by default; override with `--output-dir`.

## How to validate

```bash
just test-corpus
# or directly:
uv run --python 3.12 --with jsonschema python corpus/validate.py
```

The validator:
1. Schema-checks every `fragments/*.json` against
   `schema/extracted-fragment.schema.json`.
2. Schema-checks every `expected-route.json` against
   `schema/expected-route.schema.json`.
3. Regenerates the full corpus into a tempdir and diffs it against
   `scenarios/`. Any drift fails the run.

`just test-corpus` is wired into the root `just test` recipe.

## How to add a new axis

1. Add the values to `corpus/generator/matrix.py` (extend `ScenarioSpec`, the
   cartesian loop in `build_matrix`, and the `slug` property).
2. Thread the new field through `generator/scenario.py` and any consumers
   (`shapes.py`, `fragmenter.py`, etc.).
3. If the new axis affects the expected route, update the schemas in
   `corpus/schema/`.
4. Run `just regen-corpus`, inspect a few new scenarios, then commit. CI's
   drift check guarantees regeneration is byte-stable.
