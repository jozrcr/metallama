"""Background memory watchdog — RSS tracking, warnings, and auto-recycle.

Runs as an asyncio task started from main.py's startup handler.
Checks every running llama-server process for RSS usage and optionally
recycles if the RSS exceeds a hard limit and the server is idle.
"""

from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

memory_warnings: dict[str, str] = {}
last_recycle: dict[str, float] = {}


def _fmt(mb: float) -> str:
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB"


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

    for name, state in list(runtime_processes.items()):
        if not is_alive(state.process):
            memory_warnings.pop(name, None)
            continue

        rss_mb = process_rss_mb(state.process.pid)
        if rss_mb is None:
            continue

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
