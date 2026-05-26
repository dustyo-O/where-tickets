"""Offline tests for the cost math + the dated price table.

No network, no ``anthropic``, no AWS. Exercises per-class token billing, the
sum-over-calls helper, env-var rate overrides, and input validation.
"""

from __future__ import annotations

import pytest

from spikes.route_engine_llm.bedrock_client import Usage
from spikes.route_engine_llm.pricing import (
    MODEL_PRICE_DEFAULTS,
    ModelPrice,
    cost_usd,
    resolve_price,
    total_cost_usd,
)


def test_cost_bills_each_token_class_at_its_own_rate() -> None:
    price = ModelPrice(input=3.0, output=15.0, cache_write=3.75, cache_read=0.30)
    usage = Usage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_creation_input_tokens=1_000_000,
        cache_read_input_tokens=1_000_000,
    )
    # Each class is exactly 1M tokens, so the cost is the sum of the four rates.
    assert cost_usd(usage, price) == pytest.approx(3.0 + 15.0 + 3.75 + 0.30)


def test_cost_scales_linearly_with_token_count() -> None:
    price = ModelPrice(input=1.0, output=5.0, cache_write=1.25, cache_read=0.10)
    # 1,200 input + 80 output + 900 cache-read (the contract-test usage shape).
    usage = Usage(input_tokens=1200, output_tokens=80, cache_read_input_tokens=900)
    expected = (1200 * 1.0 + 80 * 5.0 + 900 * 0.10) / 1_000_000
    assert cost_usd(usage, price) == pytest.approx(expected)


def test_zero_usage_costs_nothing() -> None:
    price = resolve_price("opus")
    assert cost_usd(Usage(0, 0), price) == 0.0


def test_total_cost_sums_over_calls() -> None:
    price = ModelPrice(input=2.0, output=10.0, cache_write=2.5, cache_read=0.2)
    usages = [
        Usage(input_tokens=1000, output_tokens=100),
        Usage(input_tokens=2000, output_tokens=200),
    ]
    expected = cost_usd(usages[0], price) + cost_usd(usages[1], price)
    assert total_cost_usd(usages, price) == pytest.approx(expected)
    assert total_cost_usd([], price) == 0.0


@pytest.mark.parametrize("alias", sorted(MODEL_PRICE_DEFAULTS))
def test_resolve_price_returns_positive_rates_for_every_alias(alias: str) -> None:
    price = resolve_price(alias)
    assert price.input > 0
    assert price.output > 0
    assert price.cache_write > 0
    assert price.cache_read > 0


def test_resolve_price_is_case_insensitive() -> None:
    assert resolve_price("HAIKU") == resolve_price("haiku")


def test_resolve_price_rejects_unknown_alias() -> None:
    with pytest.raises(ValueError, match="unknown model alias"):
        resolve_price("gpt")


def test_env_override_replaces_a_single_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SPIKE_PRICE_HAIKU_INPUT", "9.99")
    price = resolve_price("haiku")
    assert price.input == pytest.approx(9.99)
    # Untouched rates fall back to the dated defaults.
    assert price.output == MODEL_PRICE_DEFAULTS["haiku"].output


def test_env_override_rejects_non_numeric(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPIKE_PRICE_OPUS_OUTPUT", "free")
    with pytest.raises(ValueError, match="not a valid"):
        resolve_price("opus")


def test_env_override_rejects_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPIKE_PRICE_SONNET_CACHE_READ", "-1")
    with pytest.raises(ValueError, match="non-negative"):
        resolve_price("sonnet")
