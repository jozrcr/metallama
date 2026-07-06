"""Tests for metallama.app.unified_config — config round-trip and CRUD."""
import pytest
from pathlib import Path

from metallama.app.unified_config import (
    UnifiedConfig,
    ManagedServer,
    Preset,
    ModelAlias,
    load_unified_config,
    save_unified_config,
    find_preset,
    add_preset,
    delete_preset,
    clear_config_cache,
    DEFAULT_PRESETS,
)


def _make_config():
    return UnifiedConfig(
        engine_defaults={"llama": ["--temp 0.8"]},
        managed_servers=[
            ManagedServer(name="srv1", model_path="/models/m.gguf", port=8080, preset="coding")
        ],
        presets=[
            Preset(
                name="coding",
                description='Has "quotes" and colons: \\backslash',
                context_window=32000,
                parallel=2,
                extra_args=["--temp 0.2"],
                system_prompt="You are a coder.\nKeep diffs minimal.",
            )
        ],
        aliases=[
            ModelAlias(name="qwen-coder", server="srv1", preset="coding"),
        ],
    )


def test_round_trip_config(tmp_path: Path):
    cfg = _make_config()
    p = tmp_path / "config.yaml"
    save_unified_config(cfg, p)

    clear_config_cache()
    loaded = load_unified_config(p)

    # Engine defaults
    assert loaded.engine_defaults["llama"] == ["--temp 0.8"]

    # Managed server
    srv = loaded.managed_servers[0]
    assert srv.name == "srv1"
    assert srv.model_path == "/models/m.gguf"
    assert srv.port == 8080
    assert srv.preset == "coding"

    # Preset
    preset = loaded.presets[0]
    assert preset.name == "coding"
    assert 'quotes' in preset.description
    assert preset.context_window == 32000
    assert preset.parallel == 2
    assert preset.system_prompt == "You are a coder.\nKeep diffs minimal."

    # Alias
    alias = loaded.aliases[0]
    assert alias.name == "qwen-coder"
    assert alias.server == "srv1"


def test_find_preset_config_shadows_default(tmp_path: Path):
    default_name = DEFAULT_PRESETS[0].name  # e.g. "agentic-coding"
    cfg = UnifiedConfig(
        presets=[Preset(name=default_name, description="shadowed")]
    )
    p = tmp_path / "config.yaml"
    save_unified_config(cfg, p)
    clear_config_cache()

    loaded = load_unified_config(p)
    found = find_preset(loaded, default_name)
    assert found is not None
    assert found.description == "shadowed"


def test_find_preset_unknown_returns_none():
    cfg = UnifiedConfig()
    assert find_preset(cfg, "does-not-exist") is None


def test_find_preset_seed_when_config_empty():
    cfg = UnifiedConfig()
    for dp in DEFAULT_PRESETS:
        found = find_preset(cfg, dp.name)
        assert found is not None
        assert found.name == dp.name


def test_load_malformed_yaml_returns_empty(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text(":\n  - :\n    [[invalid yaml{{{{\n")
    clear_config_cache()
    cfg = load_unified_config(p)
    assert isinstance(cfg, UnifiedConfig)
    assert cfg.managed_servers == []


def test_delete_preset_referenced_raises(tmp_path: Path):
    cfg = UnifiedConfig(
        presets=[Preset(name="my-preset")],
        managed_servers=[ManagedServer(name="srv1", model_path="/m.gguf", port=8080, preset="my-preset")],
    )
    p = tmp_path / "config.yaml"
    save_unified_config(cfg, p)
    clear_config_cache()

    with pytest.raises(ValueError, match="referenced"):
        delete_preset("my-preset", p)


def test_delete_preset_unreferenced_ok(tmp_path: Path):
    cfg = UnifiedConfig(
        presets=[Preset(name="orphan")],
    )
    p = tmp_path / "config.yaml"
    save_unified_config(cfg, p)
    clear_config_cache()

    delete_preset("orphan", p)
    clear_config_cache()
    loaded = load_unified_config(p)
    assert loaded.presets == []
