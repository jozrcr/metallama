"""Tests for crash auto-restart decision logic (watchdog._should_restart)."""

from __future__ import annotations

import time

from metallama.app.watchdog import _should_restart


def test_budget_respected():
    """When attempt >= max_attempts, should_restart returns False."""
    now = time.time()
    exit_at_ms = int((now - 300) * 1000)  # crashed 5 minutes ago
    assert _should_restart(exit_at_ms, 0, 2, now) is True
    assert _should_restart(exit_at_ms, 1, 2, now) is True
    assert _should_restart(exit_at_ms, 2, 2, now) is False  # attempt == max
    assert _should_restart(exit_at_ms, 5, 2, now) is False  # way over


def test_backoff_attempt_zero():
    """Attempt 0 requires 60 seconds backoff."""
    now = time.time()
    exit_at_ms = int((now - 30) * 1000)  # crashed 30s ago
    assert _should_restart(exit_at_ms, 0, 5, now) is False  # only 30s < 60s

    exit_at_ms = int((now - 60) * 1000)  # crashed exactly 60s ago
    assert _should_restart(exit_at_ms, 0, 5, now) is True


def test_backoff_attempt_one():
    """Attempt 1 requires 120 seconds backoff."""
    now = time.time()
    exit_at_ms = int((now - 90) * 1000)  # crashed 90s ago
    assert _should_restart(exit_at_ms, 1, 5, now) is False  # 90s < 120s

    exit_at_ms = int((now - 120) * 1000)  # crashed 120s ago
    assert _should_restart(exit_at_ms, 1, 5, now) is True


def test_backoff_attempt_two():
    """Attempt 2 requires 180 seconds backoff."""
    now = time.time()
    exit_at_ms = int((now - 100) * 1000)  # crashed 100s ago
    assert _should_restart(exit_at_ms, 2, 5, now) is False  # 100s < 180s

    exit_at_ms = int((now - 180) * 1000)  # crashed 180s ago
    assert _should_restart(exit_at_ms, 2, 5, now) is True


def test_exhausted_budget_forever():
    """Once budget is exhausted, should_restart stays False regardless of time."""
    now = time.time()
    exit_at_ms = int((now - 3600) * 1000)  # crashed 1 hour ago
    assert _should_restart(exit_at_ms, 2, 2, now) is False
    assert _should_restart(exit_at_ms, 100, 2, now) is False


def test_zero_max_attempts():
    """max_attempts=0 means restart is disabled — always False."""
    now = time.time()
    exit_at_ms = int((now - 300) * 1000)
    assert _should_restart(exit_at_ms, 0, 0, now) is False


def test_just_before_backoff_boundary():
    """Edge case: 1ms before backoff deadline should be False."""
    now = time.time()
    # exit_at_ms such that elapsed = 59.999s (just under 60s)
    exit_at_ms = int(now * 1000 - 59999)
    assert _should_restart(exit_at_ms, 0, 5, now) is False
