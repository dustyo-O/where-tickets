"""Dated per-model USD price table + cost-from-usage computation.

Cost in this spike is an explicit **estimate**: a run reports what it would
*roughly* cost, computed from the token usage Bedrock returns per call. Prices
drift, so the table is:

- **Dated and source-attributed** (see ``# Prices as of …`` below).
- **Isolated** in this one module (technical-considerations §2.6 risk note).
- **Env-overridable** per rate, so an operator can correct a stale number for a
  given account/region without editing code — mirroring the model-id override
  pattern in :mod:`spikes.route_engine_llm.bedrock_client`.

Rates are USD **per 1,000,000 tokens** (the unit AWS publishes). Four rates per
model: input, output, cache-write (5-minute TTL), cache-read. Cache-write is
1.25× input and cache-read 0.1× input for current Claude models — the standard
Anthropic prompt-caching multipliers (e.g. Sonnet 4.6: input $3.00, cache-write
$3.75, cache-read $0.30).

**Caveat — regional premium:** these are the standard *global*-endpoint rates.
The spike calls Claude through EU cross-region inference profiles (the ``eu.``
prefix in :mod:`bedrock_client`), and for Claude 4.5+ regional/multi-region
endpoints carry a ~10% premium over global. Reported cost is therefore a slight
*under*-estimate for those profiles; bump the rates via env (or switch to the
``global.`` profiles) if you need the premium reflected exactly.

Override any rate via ``SPIKE_PRICE_<ALIAS>_<KIND>`` where ``<KIND>`` is one of
``INPUT`` / ``OUTPUT`` / ``CACHE_WRITE`` / ``CACHE_READ`` and the value is USD
per 1M tokens, e.g. ``SPIKE_PRICE_HAIKU_INPUT=1.10``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterable

    from spikes.route_engine_llm.bedrock_client import Usage

__all__ = [
    "PRICING_AS_OF",
    "PRICING_SOURCE",
    "ModelPrice",
    "MODEL_PRICE_DEFAULTS",
    "resolve_price",
    "cost_usd",
    "total_cost_usd",
]

# Prices as of 2026-05-26, verified against the official Anthropic pricing page
# (standard global-endpoint, on-demand rates; Bedrock matches Claude API list
# pricing). Per the spec, cost is an estimate and these rates drift — override
# per-rate via env (see module docs). See also the ~10% US-regional caveat above.
PRICING_AS_OF = "2026-05-26"
PRICING_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """USD rates **per 1,000,000 tokens** for one model alias.

    ``cache_write`` is the 5-minute-TTL write rate; ``cache_read`` the cached
    read rate. All four are positive USD-per-1M-token figures.
    """

    input: float
    output: float
    cache_write: float
    cache_read: float


# Per-1M-token USD rates for the current Claude generation (verified 2026-05-26):
# Opus 4.5/4.6/4.7 = 5/25, Sonnet 4.6 = 3/15, Haiku 4.5 = 1/5 (input/output).
# Cache-write = 1.25× input, cache-read = 0.1× input — the standard Anthropic
# prompt-caching multipliers. Standard global-endpoint rates (see regional caveat).
MODEL_PRICE_DEFAULTS: dict[str, ModelPrice] = {
    "opus": ModelPrice(
        input=5.00,
        output=25.00,
        cache_write=6.25,
        cache_read=0.50,
    ),
    "sonnet": ModelPrice(
        input=3.00,
        output=15.00,
        cache_write=3.75,
        cache_read=0.30,
    ),
    "haiku": ModelPrice(
        input=1.00,
        output=5.00,
        cache_write=1.25,
        cache_read=0.10,
    ),
}

# One million tokens — the unit AWS quotes prices in.
_PER = 1_000_000.0

# Map a ModelPrice field to the env-var suffix that overrides it.
_RATE_ENV_SUFFIX: dict[str, str] = {
    "input": "INPUT",
    "output": "OUTPUT",
    "cache_write": "CACHE_WRITE",
    "cache_read": "CACHE_READ",
}


def _env_rate(alias: str, field: str, default: float) -> float:
    """Return the env-overridden rate for ``alias``/``field`` or ``default``.

    Env var: ``SPIKE_PRICE_<ALIAS>_<KIND>`` (USD per 1M tokens). A malformed or
    negative value raises ``ValueError`` so a typo fails loudly rather than
    silently mispricing a run.
    """
    name = f"SPIKE_PRICE_{alias.upper()}_{_RATE_ENV_SUFFIX[field]}"
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        msg = f"{name}={raw!r} is not a valid USD-per-1M-token number"
        raise ValueError(msg) from exc
    if value < 0:
        msg = f"{name}={raw!r} must be non-negative"
        raise ValueError(msg)
    return value


def resolve_price(alias: str) -> ModelPrice:
    """Return the (possibly env-overridden) :class:`ModelPrice` for ``alias``.

    ``alias`` is one of ``opus`` / ``sonnet`` / ``haiku`` (case-insensitive).
    """
    key = alias.lower()
    if key not in MODEL_PRICE_DEFAULTS:
        valid = ", ".join(sorted(MODEL_PRICE_DEFAULTS))
        msg = f"unknown model alias {alias!r}; expected one of: {valid}"
        raise ValueError(msg)
    base = MODEL_PRICE_DEFAULTS[key]
    return ModelPrice(
        input=_env_rate(key, "input", base.input),
        output=_env_rate(key, "output", base.output),
        cache_write=_env_rate(key, "cache_write", base.cache_write),
        cache_read=_env_rate(key, "cache_read", base.cache_read),
    )


def cost_usd(usage: Usage, price: ModelPrice) -> float:
    """Estimated USD cost of a single call's ``usage`` under ``price``.

    Each token class is billed at its own rate. ``input_tokens`` are the
    non-cached input tokens Bedrock reports separately from cache read/write
    tokens, so the four classes are summed without double-counting.
    """
    return (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_creation_input_tokens * price.cache_write
        + usage.cache_read_input_tokens * price.cache_read
    ) / _PER


def total_cost_usd(usages: Iterable[Usage], price: ModelPrice) -> float:
    """Estimated USD cost summed across many calls under one ``price``."""
    return sum(cost_usd(u, price) for u in usages)
