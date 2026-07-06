"""Parse llama.cpp server logs for tokens-per-second metrics.

llama.cpp appends a summary line at the end of each request:
  "decoded 1234 tokens in 12.34 seconds, 100.12 tokens per second"
  "prompt eval: 1234 tokens in 1.23 seconds, 1004.88 tokens per second"

This module exposes ``feed_line()`` for the log-tail loop and returns
a simple dict consumed by the frontend.
"""

from __future__ import annotations

import re
from typing import Dict, Optional

# Match lines like:
#   "decoded 1234 tokens in 12.34 seconds, 100.12 tokens per second"
#   "prompt eval: 1234 tokens in 1.23 seconds, 1004.88 tokens per second"
_DECODING_RE = re.compile(
    r"decoded\s+\d+\s+tokens\s+in\s+[\d.]+\s+seconds,\s+([\d.]+)\s+tokens\s+per\s+second"
)
_PROMPT_RE = re.compile(
    r"prompt\s+eval:\s+\d+\s+tokens\s+in\s+[\d.]+\s+seconds,\s+([\d.]+)\s+tokens\s+per\s+second"
)

latest_speeds: Dict[str, Dict[str, Optional[float]]] = {}


def feed_line(model_name: str, text: str) -> None:
    """Parse a single log line for speed metrics and update ``latest_speeds``."""
    try:
        entry = latest_speeds.setdefault(model_name, {"pp_tps": None, "gen_tps": None})

        m_prompt = _PROMPT_RE.search(text)
        if m_prompt:
            entry["pp_tps"] = float(m_prompt.group(1))
            return

        m_decoded = _DECODING_RE.search(text)
        if m_decoded:
            entry["gen_tps"] = float(m_decoded.group(1))
            return

    except Exception:
        # Parser must never raise — log-tail loop stays alive
        pass


def clear_speed(model_name: str) -> None:
    """Forget cached speeds when a server is stopped."""
    latest_speeds.pop(model_name, None)
