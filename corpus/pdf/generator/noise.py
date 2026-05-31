"""Bounded randomized noise for one scenario.

This is the ONLY randomized module in the PDF corpus generator. The contract
is intentionally narrow:

- A scenario carries a stable ``noise_seed`` set by the matrix from its axis
  inputs (see ``corpus/pdf/generator/matrix.py``).
- ``pick_noise(noise_seed)`` consumes that seed via ``random.Random(seed)``
  and returns a ``NoiseChoices`` instance — a typed, frozen bundle of all
  layout decisions for that scenario.
- Every choice is drawn from a small, fixed catalog declared at module top
  (banners, T&C blocks, footer-ad variants, font pairs, partial inclusion
  orders). Out-of-bounds outputs are a bug: the Phase-C smoke test asserts
  every drawn value is a member of its catalog.

Critically: noise picks WHICH partial / CSS / font to include — never WHAT
data goes into the document. Data is fully owned by ``data.py``. A noise
choice mutating a date, traveler, price, or QR payload is wrong.

Catalog-size limits (per technical-considerations §2.3):

- Marketing-banner count: capped at 2 (0..2 inclusive).
- T&C blocks: capped at 1 (0 or 1 block).
- Footer ad variants: small (~3) named pool.
- Font pairs: small (~3) named pool. One font is bundled (Inter); the
  remaining pairs fall back to sans-serif system stacks at render time.
- Partial inclusion order: permutation over a fixed short list of optional
  partials so the order they appear in is itself a bounded noise axis.
- Secondary QR placement: bool — a layout-level switch only; the QR payload
  itself comes from data.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Catalogs. All noise outputs MUST be members of one of these.
# ---------------------------------------------------------------------------

# Marketing-banner copy. The generator picks 0..MAX_MARKETING_BANNERS of these
# (deterministically by index, see `pick_noise`).
MARKETING_BANNERS: tuple[str, ...] = (
    "Earn 2x miles on your next booking — Vela Club members only.",
    "Add a premium seat now and save 15%.",
    "Lounge access from EUR 19 — upgrade at the gate.",
)
MAX_MARKETING_BANNERS: int = 2

# Terms & conditions blocks. Either zero or one is included. Content is
# clearly marked fictional in the template partial so reviewers don't mistake
# the corpus for a real legal document.
TC_BLOCKS: tuple[str, ...] = (
    "fare-rules",
    "baggage-policy",
    "covid-advisory",
)

# Footer ad variants — named so the template can branch on the string.
FOOTER_VARIANTS: tuple[str, ...] = (
    "ad-rental-car",
    "ad-travel-insurance",
    "ad-hotel-bundle",
)

# Font pair names — each maps to a CSS class in templates/styles/vela-air.css.
# "Inter / Inter" is the bundled OFL font; the other pairs fall back to system
# sans-serif stacks declared in the CSS.
FONT_PAIRS: tuple[str, ...] = (
    "inter-inter",
    "inter-system",
    "system-system",
)

# Optional partials whose render order itself becomes a (bounded) noise axis.
# The generator picks a permutation of this tuple; the template iterates over
# `noise.partial_order` and includes each one. The actual *inclusion* of a
# given partial may also be gated by another noise field (e.g. tc_block).
PARTIAL_INCLUSION_ORDER: tuple[str, ...] = (
    "marketing_banner",
    "tc_block",
    "footer_ad",
)


# ---------------------------------------------------------------------------
# Choices bundle.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NoiseChoices:
    """Typed, frozen bundle of all noise decisions for one scenario.

    Every field is drawn from a fixed catalog above. The template reads
    these to gate `{% include %}` blocks and to pick CSS classes. Nothing
    here ever touches scenario *data*.
    """

    # How many marketing banners to render (0..MAX_MARKETING_BANNERS).
    marketing_banner_count: int
    # Optional T&C block name (``None`` if no T&C is included this run).
    tc_block: str | None
    # Footer ad variant — always one (footers are mandatory branding chrome).
    footer_variant: str
    # Named font pair from FONT_PAIRS.
    font_pair: str
    # Permutation of PARTIAL_INCLUSION_ORDER; the template iterates it.
    partial_order: tuple[str, ...]
    # Whether to render the QR block at the side (False) or at the bottom
    # (True). Pure layout switch; payload is owned by data.py.
    secondary_qr_placement: bool


# ---------------------------------------------------------------------------
# Pure picker.
# ---------------------------------------------------------------------------


def pick_noise(noise_seed: int) -> NoiseChoices:
    """Return a deterministic ``NoiseChoices`` for the given seed.

    Pure function: same ``noise_seed`` -> same ``NoiseChoices``, always. The
    seed is set by the matrix from stable axis input, so re-running the
    generator produces the same noise unless the seed is overridden.
    """
    rng = random.Random(noise_seed)

    marketing_banner_count = rng.randint(0, MAX_MARKETING_BANNERS)

    # 50/50 whether to include a T&C block at all; when included, pick one.
    if rng.random() < 0.5:
        tc_block: str | None = None
    else:
        tc_block = rng.choice(TC_BLOCKS)

    footer_variant = rng.choice(FOOTER_VARIANTS)
    font_pair = rng.choice(FONT_PAIRS)

    partials = list(PARTIAL_INCLUSION_ORDER)
    rng.shuffle(partials)
    partial_order: tuple[str, ...] = tuple(partials)

    secondary_qr_placement = rng.random() < 0.5

    return NoiseChoices(
        marketing_banner_count=marketing_banner_count,
        tc_block=tc_block,
        footer_variant=footer_variant,
        font_pair=font_pair,
        partial_order=partial_order,
        secondary_qr_placement=secondary_qr_placement,
    )


__all__ = [
    "MARKETING_BANNERS",
    "MAX_MARKETING_BANNERS",
    "TC_BLOCKS",
    "FOOTER_VARIANTS",
    "FONT_PAIRS",
    "PARTIAL_INCLUSION_ORDER",
    "NoiseChoices",
    "pick_noise",
]
