"""Unit tests for :mod:`where_tickets.extraction.bedrock` — no live Bedrock.

These tests run in BOTH the default backend venv (no ``extraction`` group)
AND the extraction-group venv. The whole point is to verify the lazy-import
contract: ``bedrock`` must be importable, ``resolve_model_id`` must work, and
``make_client`` must raise a clear :class:`ImportError` when ``anthropic`` is
absent. So there is intentionally NO ``pytest.importorskip("anthropic")``.

The instance-method paths (``complete_text`` / ``complete_vision``) need a
live SDK response shape and are exercised end-to-end against real Bedrock in
Slice 9 — not here.
"""

from __future__ import annotations

import sys

import pytest

from where_tickets.extraction.bedrock import (
    MODEL_PROFILE_DEFAULTS,
    make_client,
    resolve_model_id,
)


# --------------------------------------------------------------------------- #
# resolve_model_id
# --------------------------------------------------------------------------- #


def test_resolve_haiku_default_is_eu_inference_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no env override, ``haiku`` resolves to the documented EU profile."""
    monkeypatch.delenv("WT_BEDROCK_MODEL_HAIKU", raising=False)
    assert (
        resolve_model_id("haiku")
        == "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
    )
    # Sanity-check the constant matches so the contract is enforced in one place.
    assert MODEL_PROFILE_DEFAULTS["haiku"] == resolve_model_id("haiku")


def test_resolve_sonnet_default_is_eu_inference_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no env override, ``sonnet`` resolves to the documented EU profile."""
    monkeypatch.delenv("WT_BEDROCK_MODEL_SONNET", raising=False)
    assert resolve_model_id("sonnet") == "eu.anthropic.claude-sonnet-4-6"
    assert MODEL_PROFILE_DEFAULTS["sonnet"] == resolve_model_id("sonnet")


def test_resolve_haiku_env_override_is_honored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``WT_BEDROCK_MODEL_HAIKU`` overrides the default haiku profile id."""
    monkeypatch.setenv("WT_BEDROCK_MODEL_HAIKU", "custom-haiku-id")
    assert resolve_model_id("haiku") == "custom-haiku-id"


def test_resolve_sonnet_env_override_is_honored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``WT_BEDROCK_MODEL_SONNET`` overrides the default sonnet profile id."""
    monkeypatch.setenv("WT_BEDROCK_MODEL_SONNET", "custom-sonnet-id")
    assert resolve_model_id("sonnet") == "custom-sonnet-id"


def test_resolve_alias_is_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upper-case aliases resolve like their lower-case forms."""
    monkeypatch.delenv("WT_BEDROCK_MODEL_HAIKU", raising=False)
    monkeypatch.delenv("WT_BEDROCK_MODEL_SONNET", raising=False)
    assert resolve_model_id("HAIKU") == resolve_model_id("haiku")
    assert resolve_model_id("Sonnet") == resolve_model_id("sonnet")


def test_resolve_unknown_alias_raises_with_alias_in_message() -> None:
    """An unknown alias surfaces as ``ValueError`` mentioning the bad value."""
    with pytest.raises(ValueError, match=r"unknown model alias 'opus'"):
        resolve_model_id("opus")


# --------------------------------------------------------------------------- #
# make_client lazy-import contract
# --------------------------------------------------------------------------- #


def test_make_client_raises_clear_import_error_when_anthropic_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``make_client`` raises ``ImportError`` with an install hint when
    ``anthropic`` cannot be imported.

    Simulated by stuffing ``None`` into ``sys.modules["anthropic"]``, which
    makes the lazy ``from anthropic import ...`` raise ``ImportError`` even
    when the package is actually installed in the venv. That keeps this test
    deterministic in both venvs (default + ``--group extraction``).
    """
    monkeypatch.setitem(sys.modules, "anthropic", None)

    with pytest.raises(ImportError, match=r"uv sync --group extraction"):
        make_client()
