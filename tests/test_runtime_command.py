"""Tests for metallama.app.runtime — build_command_preview merge precedence."""
from unittest.mock import patch, MagicMock
import pytest

from metallama.app.runtime import build_command_preview, _strip_flag
from metallama.app.models import ModelProfile


def _make_profile(**overrides):
    defaults = dict(
        name="test-srv",
        model_path="/models/test.gguf",
        port=8080,
        engine="llama",
        context_window=4096,
        parallel=1,
        extra_args=[],
        preset=None,
        model_draft=None,
    )
    defaults.update(overrides)
    return ModelProfile(**defaults)


def test_strip_flag():
    # Separate flag + value args
    assert _strip_flag(["--temp", "0.8", "--ctx-size", "4096"], "--ctx-size") == ["--temp", "0.8"]
    # Equals-style flag
    assert _strip_flag(["--ctx-size=4096"], "--ctx-size") == []
    # Multiple occurrences removed
    assert _strip_flag(["--temp", "0.8", "--ctx-size", "1024", "--port", "8080"], "--ctx-size") == ["--temp", "0.8", "--port", "8080"]


def test_command_merge_precedence_last_wins():
    """Engine default --temp 0.9, preset --temp 0.2, server --temp 0.6 → all three present."""
    profile = _make_profile(extra_args=["--temp 0.6"])

    with patch("metallama.app.runtime._get_engine_default_args") as mock_engine:
        with patch("metallama.app.runtime._get_preset_extra_args") as mock_preset:
            with patch("metallama.app.runtime.Config") as mock_config:
                mock_config.BIND_HOST = "127.0.0.1"
                mock_engine.return_value = ["--temp 0.9"]
                mock_preset.return_value = ["--temp 0.2"]

                cmd, _ = build_command_preview(profile)

                # All three --temp flags should be present in order
                temp_indices = [i for i, arg in enumerate(cmd) if arg == "--temp"]
                assert len(temp_indices) == 3
                assert cmd[temp_indices[0] + 1] == "0.9"  # engine default
                assert cmd[temp_indices[1] + 1] == "0.2"  # preset
                assert cmd[temp_indices[2] + 1] == "0.6"  # server (last wins at runtime)


def test_ctx_size_computed_from_context_window_and_parallel():
    profile = _make_profile(context_window=8192, parallel=4)

    with patch("metallama.app.runtime._get_engine_default_args") as mock_engine:
        with patch("metallama.app.runtime._get_preset_extra_args") as mock_preset:
            with patch("metallama.app.runtime.Config") as mock_config:
                mock_config.BIND_HOST = "127.0.0.1"
                mock_engine.return_value = []
                mock_preset.return_value = []

                cmd, _ = build_command_preview(profile)

                # --ctx-size should be context_window * parallel = 32768
                ctx_idx = cmd.index("--ctx-size")
                assert cmd[ctx_idx + 1] == "32768"


def test_host_from_config_and_extra_args_host_appended():
    profile = _make_profile(extra_args=["--host 0.0.0.0"])

    with patch("metallama.app.runtime._get_engine_default_args") as mock_engine:
        with patch("metallama.app.runtime._get_preset_extra_args") as mock_preset:
            with patch("metallama.app.runtime.Config") as mock_config:
                mock_config.BIND_HOST = "127.0.0.1"
                mock_engine.return_value = []
                mock_preset.return_value = []

                cmd, _ = build_command_preview(profile)

                # First --host should be from config, second from extra_args
                host_indices = [i for i, arg in enumerate(cmd) if arg == "--host"]
                assert len(host_indices) == 2
                assert cmd[host_indices[0] + 1] == "127.0.0.1"
                assert cmd[host_indices[1] + 1] == "0.0.0.0"
