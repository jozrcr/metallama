"""Tests for log rotation behaviour in metallama.app.logs."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from metallama.app import logs


def _log_file_path(model_name: str) -> Path:
    return logs.LOGS_DIR / f"{model_name}.log"


def test_begin_capture_fills_ring(tmp_path: Path, monkeypatch):
    """First begin_capture writes to ring buffer and .log file."""
    monkeypatch.setattr(logs, "LOGS_DIR", tmp_path)
    proc = subprocess.Popen(["sh", "-c", "cat"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    logs.begin_capture("test-model", proc)
    # Give it a moment to settle.
    time.sleep(0.05)
    # The .log file should exist.
    log_file = tmp_path / "test-model.log"
    assert log_file.exists(), "Log file should be created by begin_capture"
    proc.terminate()
    proc.wait()


def test_second_begin_capture_rotates(tmp_path: Path, monkeypatch):
    """Second begin_capture rotates .log to .log.1."""
    monkeypatch.setattr(logs, "LOGS_DIR", tmp_path)
    proc = subprocess.Popen(["sh", "-c", "cat"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    logs.begin_capture("test-model", proc)
    log_file = tmp_path / "test-model.log"
    assert log_file.exists()
    # Write something to the log file directly to simulate content.
    log_file.write_text("line1\nline2\n")
    # Second capture should rotate.
    logs.begin_capture("test-model", proc)
    rotated = tmp_path / "test-model.log.1"
    assert rotated.exists(), "Second begin_capture should rotate .log to .log.1"
    assert rotated.read_text() == "line1\nline2\n"
    proc.terminate()
    proc.wait()
