"""Unit tests for the central model registry (shared.models).

Covers tier resolution + the function-calling robustness config that keeps
ADK from choking on gemini-2.5's compositional/<ctrl>-token tool calls.
"""
from __future__ import annotations

from shared.models import (
    HEAVY,
    LIGHT,
    gen_content_config,
    model_for,
)


def _clear(monkeypatch):
    for v in ("LOCAL_OVERRIDE_MODEL", "MODEL_HEAVY", "MODEL_LIGHT"):
        monkeypatch.delenv(v, raising=False)


def test_builtin_defaults(monkeypatch):
    _clear(monkeypatch)
    assert model_for(HEAVY) == "gemini-2.5-flash"
    assert model_for(LIGHT) == "gemini-2.5-flash-lite"


def test_per_tier_env_override(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("MODEL_HEAVY", "gemini-3.5-flash")
    monkeypatch.setenv("MODEL_LIGHT", "gemini-3.1-flash-lite")
    assert model_for(HEAVY) == "gemini-3.5-flash"
    assert model_for(LIGHT) == "gemini-3.1-flash-lite"


def test_local_override_forces_both_tiers(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("MODEL_HEAVY", "gemini-3.5-flash")
    monkeypatch.setenv("LOCAL_OVERRIDE_MODEL", "gemini-2.0-flash")
    assert model_for(HEAVY) == "gemini-2.0-flash"
    assert model_for(LIGHT) == "gemini-2.0-flash"


def test_legacy_literal_resolves_through_tier(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("MODEL_HEAVY", "gemini-3.5-flash")
    # A legacy literal maps to its tier, then resolves via env — so a stray
    # hardcoded name still routes centrally.
    assert model_for("gemini-3.5-flash") == "gemini-3.5-flash"
    assert model_for("gemini-2.5-flash") == "gemini-3.5-flash"


def test_unknown_literal_passthrough(monkeypatch):
    _clear(monkeypatch)
    assert model_for("gemini-9.9-experimental") == "gemini-9.9-experimental"


def test_gen_content_config_always_auto_tool_calls():
    for m in ("gemini-3.5-flash", "gemini-2.5-flash", "gemini-2.5-flash-lite"):
        cfg = gen_content_config(m)
        assert cfg.tool_config.function_calling_config.mode.value == "AUTO"


def test_gen_content_config_disables_thinking_for_25_flash():
    # gemini-2.5-flash leaks <ctrl> tokens / compositional calls with thinking
    # on — disable it so ADK gets clean structured calls.
    for m in ("gemini-2.5-flash", "gemini-2.5-flash-lite"):
        cfg = gen_content_config(m)
        assert cfg.thinking_config is not None
        assert cfg.thinking_config.thinking_budget == 0


def test_gen_content_config_pro_uses_min_thinking_budget():
    # gemini-2.5-pro REJECTS thinking_budget=0 (HTTP 400) — pro can't disable
    # thinking, so use the minimum valid budget instead of 0.
    cfg = gen_content_config("gemini-2.5-pro")
    assert cfg.thinking_config is not None
    assert cfg.thinking_config.thinking_budget == 128


def test_gen_content_config_keeps_thinking_for_3x():
    # gemini-3.x parses tool calls cleanly; leave its (default) thinking alone.
    cfg = gen_content_config("gemini-3.5-flash")
    assert cfg.thinking_config is None
