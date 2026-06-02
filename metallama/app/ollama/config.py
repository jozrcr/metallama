from __future__ import annotations

import os
from pathlib import Path

import yaml

from .schemas import AppConfig, SubserverConfig


def load_config(path: str | os.PathLike = "config.yaml") -> AppConfig:
    config_path = Path(path)
    if not config_path.is_absolute():
        # Resolve relative to this file's directory so the app can be run from anywhere.
        config_path = Path(__file__).parent / config_path

    with config_path.open() as fh:
        raw = yaml.safe_load(fh)

    subservers = []
    for entry in raw.get("subservers", []):
        if "url" not in entry:
            entry["url"] = f"http://localhost:{entry['port']}"
        subservers.append(SubserverConfig(**entry))

    return AppConfig(
        subservers=subservers,
    )
