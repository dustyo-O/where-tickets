"""Trip-bundle generator for the integration corpus (DUS-31 Slice 8).

Public surface:

- :class:`primitives.*` — typed builders that describe one PDF each
  (an air leg, a hotel stay, a supplementary venue, ...).
- :class:`composer.compose_trip` — turn an ordered list of primitives plus a
  trip slug + travelers into a :class:`composer.TripBundle` that bundles every
  per-PDF render context + ``ExtractedFields`` + the trip-level
  ``manifest.json`` + ``expected-route.json``.
- :mod:`catalog` — the curated trip set. Each entry returns a
  :class:`composer.TripSpec` consumed by the CLI.

The generator is deterministic: each call to :func:`compose_trip` with the
same :class:`TripSpec` produces byte-identical JSON. PDFs may vary at the
renderer level (mirrors the layer-1 PDF generator's behaviour).
"""

from __future__ import annotations
