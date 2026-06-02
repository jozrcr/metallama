from __future__ import annotations

from typing import Any

import httpx

from .registry import get_all_subservers
from .schemas import SubserverConfig

_PROBE_TIMEOUT = httpx.Timeout(3.0)


def _fallback_arch(current_family: str) -> str:
    if current_family and current_family != "unknown":
        return current_family
    return "llama"


def _pick_upstream_model(models: list[dict], configured_name: str) -> dict | None:
    if not models:
        return None
    for model in models:
        if model.get("id") == configured_name:
            return model
    return models[0]


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _extract_props_context_length(payload: dict[str, Any]) -> int | None:
    direct = _coerce_int(payload.get("n_ctx"))
    if direct is not None:
        return direct
    dgs = payload.get("default_generation_settings")
    if isinstance(dgs, dict):
        nested = _coerce_int(dgs.get("n_ctx"))
        if nested is not None:
            return nested
    return None


_DEFAULT_CONTEXT_LENGTH = 4096


async def probe_one(srv: SubserverConfig, client: httpx.AsyncClient) -> None:
    """Probe a single subserver and backfill its metadata in place."""
    srv.family = _fallback_arch(srv.family)
    props_ctx: int | None = None

    try:
        r_props = await client.get(f"{srv.url}/props")
        if r_props.status_code == 200:
            props_payload = r_props.json()
            if isinstance(props_payload, dict):
                props_ctx = _extract_props_context_length(props_payload)
    except (httpx.ConnectError, httpx.TimeoutException, ValueError):
        pass

    try:
        r_models = await client.get(f"{srv.url}/v1/models")
        if r_models.status_code == 200:
            models = r_models.json().get("data", [])
            selected = _pick_upstream_model(models, srv.name)
            if selected:
                meta = selected.get("meta", {}) or {}
                srv.upstream_model_id = selected.get("id", srv.name)
                srv.upstream_meta = meta

                if srv.size == 0:
                    size = _coerce_int(selected.get("size")) or _coerce_int(meta.get("size"))
                    if size is not None:
                        srv.size = size

                if srv.parameter_size == "unknown":
                    n_params = _coerce_int(meta.get("n_params"))
                    if n_params is not None:
                        srv.parameter_size = f"{round(n_params / 1e9, 1)}B"

                # Only update context_length if it wasn't explicitly set in config
                if srv.context_length == _DEFAULT_CONTEXT_LENGTH:
                    ctx = (
                        props_ctx
                        or _coerce_int(meta.get("n_ctx"))
                        or _coerce_int(meta.get("context_length"))
                        or _coerce_int(meta.get("n_ctx_train"))
                    )
                    if ctx is not None:
                        srv.context_length = ctx

                srv.family = meta.get("general.architecture", srv.family)

    except (httpx.ConnectError, httpx.TimeoutException):
        pass


async def probe_subservers() -> None:
    """Query all subservers and backfill missing metadata."""
    async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
        for srv in get_all_subservers():
            await probe_one(srv, client)
