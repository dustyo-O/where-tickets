"""Schema validator for the extracted-fields payload (single source of truth).

Loads ``corpus/pdf/schema/expected-fields.schema.json`` once at import time and
exposes:

- :data:`EXTRACTOR_SCHEMA` — the corpus schema with the corpus-only metadata
  fields (``scenario_id`` / ``noise_seed``) removed from ``required``. The
  property definitions are kept intact (the extractor never emits these keys
  so removing them is unnecessary; keeping them means a future caller could
  send them without re-jigging the schema). This is the same schema fed to
  Anthropic's tool ``input_schema`` over in :mod:`prompts`.
- :func:`validate` — runs the schema against a payload and returns
  ``(ok, errors)``. ``ok = False`` cascades into the documented fallbacks per
  tech-spec §2.2 (schema mismatch → Sonnet text fallback, etc.).
- :data:`SCHEMA_FILE` — the absolute path to the JSON file (handy for tests
  asserting "the source of truth is THAT file").

Path resolution: ``backend/`` sits next to the ``corpus/`` tree in the repo, so
``Path(__file__).resolve().parents[3]`` resolves to the repo root regardless of
the CWD. This is the same shape the extraction tests use to reach the corpus
fixtures.

``jsonschema`` ships only with the optional ``extraction`` dep group, so we
import it eagerly with a pyright ignore (mirroring ``bedrock_client.py``'s
pattern). Tests that exercise this module gate themselves with
``pytest.importorskip("jsonschema")`` so the no-extraction-group test runs
collect-but-skip cleanly.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from jsonschema import (  # pyright: ignore[reportMissingImports, reportMissingModuleSource]
    Draft202012Validator,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Mapping

__all__ = [
    "EXTRACTOR_SCHEMA",
    "SCHEMA_FILE",
    "validate",
]


# Repo layout: backend/where_tickets/extraction/schema.py
#   parents[0] = extraction/
#   parents[1] = where_tickets/
#   parents[2] = backend/
#   parents[3] = <repo root>
# The corpus tree sits next to backend/ in the repo, so the schema lives at
# <repo root>/corpus/pdf/schema/expected-fields.schema.json.
SCHEMA_FILE: Final[Path] = (
    Path(__file__).resolve().parents[3]
    / "corpus"
    / "pdf"
    / "schema"
    / "expected-fields.schema.json"
)

# Corpus-metadata fields the extractor never knows about. These must be
# dropped from ``required`` before the schema is used to validate model
# output or fed to the tool ``input_schema``. ``noise_seed`` is currently
# optional in the corpus schema, but we filter it defensively so a future
# upstream change can't silently start requiring it from the extractor.
_CORPUS_METADATA_REQUIRED: Final[frozenset[str]] = frozenset(
    {"scenario_id", "noise_seed"}
)


def _load_extractor_schema() -> dict[str, Any]:
    """Load the corpus schema and strip corpus-metadata fields from ``required``."""
    raw: dict[str, Any] = json.loads(SCHEMA_FILE.read_text())
    schema = copy.deepcopy(raw)
    required = schema.get("required", [])
    schema["required"] = [k for k in required if k not in _CORPUS_METADATA_REQUIRED]
    return schema


EXTRACTOR_SCHEMA: Final[dict[str, Any]] = _load_extractor_schema()

# Build the validator once at import time; reused across every validate() call.
_VALIDATOR: Final[Draft202012Validator] = Draft202012Validator(EXTRACTOR_SCHEMA)


def _format_error(error: Any) -> str:
    """Render a jsonschema error as a single human-readable line."""
    path = "/".join(str(p) for p in error.absolute_path) or "<root>"
    return f"{path}: {error.message}"


def validate(payload: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """Validate ``payload`` against :data:`EXTRACTOR_SCHEMA`.

    Returns ``(True, [])`` on success or ``(False, errors)`` with one human-
    readable message per validation error. Callers use the boolean to decide
    whether to accept the payload or cascade into the documented fallbacks
    (schema mismatch → Sonnet text fallback, per tech-spec §2.2).
    """
    errors = sorted(_VALIDATOR.iter_errors(payload), key=lambda e: e.absolute_path)
    messages = [_format_error(e) for e in errors]
    return (not messages, messages)
