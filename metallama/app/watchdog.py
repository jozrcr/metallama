"""Background memory watchdog — RSS tracking, warnings, auto-recycle, crash restart.

Runs as an asyncio task started from main.py's startup handler.
Checks every running llama-server process for RSS usage and optionally
recycles if the RSS exceeds a hard limit and the server is idle.
Also auto-restarts crashed servers with backoff and attempt budget.
"""

from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

memory_warnings: dict[str, str] = {}
last_recycle: dict[str, float] = {}

# Crash restart state (Task 6)
_crash_streak: dict[str, int] = {}          # name -> current attempt count
_crash_last_exit_at: dict[str, float] = {}   # name -> exit timestamp (ms)
_crash_manual_reset: set[str] = set()        # names reset by manual start


def _fmt(mb: float) -> str:
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB"


# ---------------------------------------------------------------------------
# Crash auto-restart (Task 6)
# ---------------------------------------------------------------------------

STREAK_RESET_SECONDS = 600  # 10 minutes of uptime resets the streak


def _should_restart(exit_at_ms: int, attempt: int, max_attempts: int, now: float) -> bool:
    """Return True if a crash-restart attempt should be made now.

    Checks:
    - attempt < max_attempts  (budget not exhausted)
    - enough time has elapsed since exit (backoff: 60 × (attempt + 1) seconds)
    """
    if attempt >= max_attempts:
        return False
    backoff_seconds = 60 * (attempt + 1)
    elapsed = (now * 1000 - exit_at_ms) / 1000
    return elapsed >= backoff_seconds


def _reset_streak(name: str) -> None:
    """Reset crash streak for a server (called on manual start or long uptime)."""
    _crash_streak.pop(name, None)
    _crash_last_exit_at.pop(name, None)
    _crash_manual_reset.discard(name)
    memory_warnings.pop(name, None)


async def _check_crash_restart(name: str) -> None:
    """Check if a crashed server should be auto-restarted."""
    from .config import Config

    max_attempts = Config.CRASH_RESTART
    if max_attempts <= 0:
        return

    from .logs import get_unexpected_exit, append_note
    exit_info = get_unexpected_exit(name)
    if exit_info is None:
        return  # no unexpected exit (still running, or user stopped it)

    exit_at_ms = exit_info.get("at", 0)
    exit_code = exit_info.get("code", -1)

    # Track the exit timestamp on first detection
    if name not in _crash_last_exit_at:
        _crash_last_exit_at[name] = exit_at_ms
        _crash_streak[name] = 0

    attempt = _crash_streak.get(name, 0)
    now = time.time()

    # Budget exhausted → surface it and stop trying (manual start resets)
    if attempt >= max_attempts:
        memory_warnings[name] = f"crashed {max_attempts}x — auto-restart gave up"
        return

    if not _should_restart(exit_at_ms, attempt, max_attempts, now):
        return  # still waiting for backoff

    # Write note BEFORE starting (so it lands in pre-rotation log)
    note = f"[metallama] watchdog: restarting after crash (exit code {exit_code}, attempt {attempt + 1}/{max_attempts})"
    append_note(name, note)
    from .stats import record_event
    record_event(name, "crash_restart", note)
    logger.info("watchdog: crash-restart %s (%s)", name, note)

    # Increment attempt counter
    _crash_streak[name] = attempt + 1

    # Do the restart (lazy import of _start_model_core, wrapped in try/except)
    # Use _start_model_core (not start_model) so the streak counter is NOT reset.
    try:
        from .main import _start_model_core
        await _start_model_core(name)
    except Exception:
        logger.exception("watchdog: crash-restart failed for %s", name)


async def _check_streak_reset(name: str) -> None:
    """If a server has been running longer than STREAK_RESET_SECONDS, reset its streak."""
    if name not in _crash_streak:
        return
    state = None
    try:
        from .runtime import runtime_processes, is_alive
        state = runtime_processes.get(name)
    except ImportError:
        return
    if not state or not is_alive(state.process):
        return
    elapsed = time.time() - state.started_at
    if elapsed >= STREAK_RESET_SECONDS:
        logger.info("watchdog: streak reset for %s (ran %d seconds)", name, int(elapsed))
        _reset_streak(name)


async def watchdog_loop() -> None:
    """Background loop that checks RSS for every running server."""
    while True:
        try:
            await _tick()
        except Exception:
            logger.exception("watchdog tick failed")
        await asyncio.sleep(30)


async def _tick() -> None:
    from .config import Config

    # Lazy snapshot of running processes
    try:
        from .runtime import runtime_processes, is_alive, process_rss_mb
    except ImportError:
        return

    # --- RSS tracking & recycle for alive processes ---
    for name, state in list(runtime_processes.items()):
        if not is_alive(state.process):
            # Process is dead — check for crash auto-restart (Task 6)
            await _check_crash_restart(name)
            continue

        # Process is alive — check streak reset (Task 6)
        await _check_streak_reset(name)

        rss_mb = process_rss_mb(state.process.pid)
        if rss_mb is None:
            continue

        from .stats import record_sample
        record_sample(name, rss_mb)

        # --- Warn threshold ---
        warn_mb = Config.RSS_WARN_MB
        if warn_mb > 0 and rss_mb > warn_mb:
            memory_warnings[name] = f"RSS {_fmt(rss_mb)} exceeds warn threshold ({_fmt(warn_mb)})"
        else:
            memory_warnings.pop(name, None)

        # --- Recycle threshold ---
        if Config.WATCHDOG != "recycle":
            continue
        limit_mb = Config.RSS_LIMIT_MB
        if limit_mb <= 0:
            continue
        if rss_mb <= limit_mb:
            continue

        # 10-minute cooldown
        now = time.time()
        if name in last_recycle and now - last_recycle[name] < 600:
            continue

        # Check idle via /slots
        try:
            from .profiles import MODEL_PROFILES
            profile = MODEL_PROFILES.get(name)
            if not profile:
                continue

            # Never recycle a server in "starting" state; async so the
            # event loop is never blocked by a slow upstream.
            import httpx
            async with httpx.AsyncClient(timeout=2.0) as client:
                try:
                    resp = await client.get(f"http://127.0.0.1:{profile.port}/health")
                    if resp.status_code != 200:
                        continue  # still starting
                except httpx.HTTPError:
                    continue
                slots_resp = await client.get(f"http://127.0.0.1:{profile.port}/slots")
                slots_data = slots_resp.json()
                # llama.cpp returns a bare list; tolerate {"slots": [...]} too
                slots = slots_data if isinstance(slots_data, list) else slots_data.get("slots", [])
                if not slots:
                    continue
                if any(s.get("is_processing") for s in slots):
                    continue  # busy
        except Exception:
            continue  # not idle or unreachable

        # All checks passed — recycle
        log_msg = f"[metallama] watchdog: recycling (RSS {_fmt(rss_mb)} > limit {_fmt(limit_mb)})"

        # Ring buffer + log file BEFORE stopping so the reason survives rotation
        from .logs import append_note
        append_note(name, log_msg)

        last_recycle[name] = now
        logger.info("watchdog: recycling %s (RSS %s > limit %s)", name, _fmt(rss_mb), _fmt(limit_mb))
        from .stats import record_event
        record_event(name, "recycle", log_msg)

        try:
            from .main import stop_model, start_model
            await stop_model(name, _guard=None)
            # Wait until offline
            for _ in range(40):
                st = runtime_processes.get(name)
                if not st or not is_alive(st.process):
                    break
                await asyncio.sleep(0.25)
            await start_model(name, _guard=None)
        except Exception:
            logger.exception("watchdog: recycle failed for %s", name)

    # --- Crash restart for servers cleaned up from runtime_processes ---
    # cleanup_dead() (called by status_for, etc.) removes dead processes from
    # runtime_processes, so we also scan MODEL_PROFILES for unexpected exits.
    if Config.CRASH_RESTART > 0:
        try:
            from .profiles import MODEL_PROFILES
            for name in MODEL_PROFILES:
                if name in runtime_processes:
                    continue  # already handled above
                await _check_crash_restart(name)
        except ImportError:
            pass
