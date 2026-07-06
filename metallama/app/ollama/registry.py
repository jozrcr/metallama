from __future__ import annotations

from fastapi import HTTPException

from .schemas import AppConfig, SubserverConfig

_registry: dict[str, SubserverConfig] = {}


def init_registry(config: AppConfig) -> None:
    global _registry
    _registry = {srv.name: srv for srv in config.subservers}


def rebuild_registry() -> None:
    """Rebuild the gateway registry from every known server source.

    Merges (unified config wins on name conflicts):
    - managed_servers from config.yaml → routed via 127.0.0.1:<port>
    - remote_servers from config.yaml
    - legacy subservers from app/ollama/config.yaml
    - model aliases from config.yaml → routed via underlying server URL

    Probed metadata from the previous registry is carried over by URL so a
    config edit doesn't force a re-probe of running servers.
    """
    global _registry
    from ..unified_config import load_unified_config
    from .config import load_config as load_ollama_config

    merged: dict[str, SubserverConfig] = {}

    ucfg = load_unified_config()
    for s in ucfg.managed_servers:
        ctx = (s.context_window or 4096) * (s.parallel or 1)
        merged[s.name] = SubserverConfig(
            name=s.name,
            url=f"http://127.0.0.1:{s.port}",
            context_length=ctx,
            preset=s.preset,
        )
    for s in ucfg.remote_servers:
        merged.setdefault(
            s.name,
            SubserverConfig(
                name=s.name,
                url=s.url,
                family=s.family,
                context_length=s.context_length,
            ),
        )

    try:
        ocfg = load_ollama_config()
        for srv in ocfg.subservers:
            merged.setdefault(srv.name, srv)
    except Exception:
        pass

    # Register model aliases
    for alias in ucfg.aliases:
        # Resolve alias.server to its URL
        server_url: str | None = None
        server_preset: str | None = None
        for s in ucfg.managed_servers:
            if s.name == alias.server:
                server_url = f"http://127.0.0.1:{s.port}"
                server_preset = s.preset
                break
        if not server_url:
            for s in ucfg.remote_servers:
                if s.name == alias.server:
                    server_url = s.url
                    break
        if not server_url:
            # Server doesn't exist — skip silently (will be caught on use)
            continue

        # Alias preset overrides server preset for this name
        alias_preset = alias.preset if alias.preset is not None else server_preset
        merged[alias.name] = SubserverConfig(
            name=alias.name,
            url=server_url,
            preset=alias_preset,
            is_alias=True,
        )

    # Carry over probed metadata by URL
    old_by_url = {old.url.rstrip("/"): old for old in _registry.values()}
    for srv in merged.values():
        old = old_by_url.get(srv.url.rstrip("/"))
        if old is not None and old.reachable:
            srv.reachable = True
            srv.upstream_model_id = old.upstream_model_id
            srv.upstream_meta = old.upstream_meta
            srv.size = srv.size or old.size
            if srv.parameter_size == "unknown":
                srv.parameter_size = old.parameter_size
            if srv.family == "unknown":
                srv.family = old.family

    _registry = merged


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


def resolve_system_prompt(model_name: str) -> str | None:
    """Returns the system prompt of the preset attached to the alias/server, or None.

    Resolves the name the same way get_subserver does (direct name, then
    upstream_model_id fallback), then reads the preset off the resolved entry.
    Falls back to DEFAULT_PRESETS when the preset is not in config.
    """
    srv = _registry.get(model_name)
    if srv is None:
        srv = next((s for s in _registry.values() if s.upstream_model_id == model_name), None)
    if srv is None or not srv.preset:
        return None
    from ..unified_config import load_unified_config, find_preset

    preset = find_preset(load_unified_config(), srv.preset)
    return preset.system_prompt if preset else None
