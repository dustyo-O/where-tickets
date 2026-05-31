"""Smoke tests for the PDF corpus noise picker.

The contract of ``corpus.pdf.generator.noise`` is intentionally narrow: every
choice in ``NoiseChoices`` must come from one of the small named catalogs at
the top of the module, and the dataclass must never grow a field that looks
like *data* (city, date, price, ...). These tests pin all three properties.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

# Make ``corpus.pdf.generator`` importable when pytest is invoked from
# ``backend/``: ``corpus`` lives at the repo root as a PEP 420 namespace
# package.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from corpus.pdf.generator.noise import (  # type: ignore[import-not-found]  # noqa: E402  — path setup above
    FONT_PAIRS,
    FOOTER_VARIANTS,
    MAX_MARKETING_BANNERS,
    NoiseChoices,
    PARTIAL_INCLUSION_ORDER,
    TC_BLOCKS,
    pick_noise,
)


# A broad seed sweep — 1000 distinct seeds is enough to cover every catalog
# value many times over without making the suite slow.
_SWEEP_SEEDS: tuple[int, ...] = tuple(range(1000))

# Every field NoiseChoices is *allowed* to carry. Adding a real data concept
# here (city, date, price, qr_code, traveler) MUST fail the leak-guard test
# below — that is the whole point of the allowlist.
_ALLOWED_NOISE_FIELDS: frozenset[str] = frozenset(
    {
        "marketing_banner_count",
        "tc_block",
        "footer_variant",
        "font_pair",
        "partial_order",
        "secondary_qr_placement",
    }
)

# Field names that, if they ever appear on ``NoiseChoices``, indicate the
# noise layer has crossed into data territory.
_DATA_LEAK_NAMES: frozenset[str] = frozenset(
    {
        "city",
        "cities",
        "date",
        "dates",
        "datetime",
        "departure_datetime",
        "arrival_datetime",
        "price",
        "prices",
        "amount",
        "currency",
        "qr_code",
        "qr_codes",
        "traveler",
        "travelers",
        "scenario_id",
        "document_type",
    }
)


@pytest.mark.parametrize(
    "seed",
    [0, 1, 42, 2**32 - 1, 3_672_769_149_034_002_136, 9_999_999_999_999_999_999],
    ids=lambda s: f"seed={s}",
)
def test_pick_noise_is_deterministic(seed: int) -> None:
    """Same seed -> identical ``NoiseChoices`` on every call.

    The frozen dataclass already gives us value equality, so a single
    ``==`` is the whole contract.
    """
    first = pick_noise(seed)
    second = pick_noise(seed)
    assert first == second


@pytest.mark.parametrize("seed", _SWEEP_SEEDS)
def test_pick_noise_choices_are_in_catalog(seed: int) -> None:
    """Across a 1000-seed sweep, every choice must come from its catalog."""
    choices = pick_noise(seed)

    assert choices.footer_variant in FOOTER_VARIANTS, (
        f"footer_variant {choices.footer_variant!r} not in FOOTER_VARIANTS"
    )
    assert choices.font_pair in FONT_PAIRS, (
        f"font_pair {choices.font_pair!r} not in FONT_PAIRS"
    )

    # tc_block is the only optional catalog field — None is a valid choice.
    assert choices.tc_block is None or choices.tc_block in TC_BLOCKS, (
        f"tc_block {choices.tc_block!r} not in TC_BLOCKS and not None"
    )

    # partial_order must be a *permutation* of PARTIAL_INCLUSION_ORDER —
    # same elements, no duplicates, no extras.
    assert isinstance(choices.partial_order, tuple)
    assert sorted(choices.partial_order) == sorted(PARTIAL_INCLUSION_ORDER), (
        f"partial_order {choices.partial_order!r} is not a permutation of "
        f"{PARTIAL_INCLUSION_ORDER!r}"
    )

    assert isinstance(choices.secondary_qr_placement, bool)


@pytest.mark.parametrize("seed", _SWEEP_SEEDS)
def test_pick_noise_counts_are_bounded(seed: int) -> None:
    """Marketing-banner count must stay within ``[0, MAX_MARKETING_BANNERS]``.

    Anything else here would mean the catalog cap has rotted out of sync with
    the picker, which the generator would happily over-render.
    """
    choices = pick_noise(seed)
    assert 0 <= choices.marketing_banner_count <= MAX_MARKETING_BANNERS, (
        f"marketing_banner_count {choices.marketing_banner_count} not in "
        f"[0, {MAX_MARKETING_BANNERS}]"
    )


def test_noise_choices_has_no_data_field_names() -> None:
    """``NoiseChoices`` must not carry any field named after a data concept.

    Catches the "noise mutated data" bug class: if a future change adds e.g. a
    ``city`` or ``price`` field to ``NoiseChoices``, the noise layer has
    silently grown ownership of something ``data.py`` should own.
    """
    field_names = {field.name for field in dataclasses.fields(NoiseChoices)}

    leaks = field_names & _DATA_LEAK_NAMES
    assert leaks == set(), (
        f"NoiseChoices has data-shaped fields {sorted(leaks)!r}; the noise "
        "layer must never own scenario data"
    )

    extras = field_names - _ALLOWED_NOISE_FIELDS
    assert extras == set(), (
        f"NoiseChoices grew unexpected fields {sorted(extras)!r}; update the "
        "allowlist in this test only when the new field is provably "
        "layout-only"
    )
