from __future__ import annotations

from fastapi import HTTPException

from .schemas import AppConfig, SubserverConfig

_registry: dict[str, SubserverConfig] = {}


def init_registry(config: AppConfig) -> None:
    global _registry
    _registry = {srv.name: srv for srv in config.subservers}


def get_subserver(model_name: str) -> SubserverConfig:
    # First try the configured name, then fall back to the probed upstream model id.
    srv = _registry.get(model_name)
    if srv is None:
        srv = next((s for s in _registry.values() if s.upstream_model_id == model_name), None)
    if srv is None:
        raise HTTPException(status_code=404, detail={"error": "model not found"})
    return srv


def get_all_subservers() -> list[SubserverConfig]:
    return list(_registry.values())
