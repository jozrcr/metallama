from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Overridable so tests / secondary instances can use an isolated config file
# instead of the shared repo-root config.yaml.
DEFAULT_CONFIG_PATH = os.getenv("METALLAMA_CONFIG_FILE", "config.yaml")


# ---------------------------------------------------------------------------
# Presets (named launch params + system prompt)
# ---------------------------------------------------------------------------

class Preset(BaseModel):
    name: str
    description: str = ""
    context_window: int | None = None
    parallel: int | None = None
    extra_args: list[str] = Field(default_factory=list)
    system_prompt: str | None = None


class ModelAlias(BaseModel):
    name: str          # the model name clients see, e.g. "qwen-coder"
    server: str        # name of a managed or remote server
    preset: str | None = None  # preset whose system_prompt applies to this alias


# ---------------------------------------------------------------------------
# Default presets (seeded if not overridden in config)
# ---------------------------------------------------------------------------

DEFAULT_PRESETS: list[Preset] = [
    Preset(
        name="agentic-coding",
        description="Tool-using coding agent",
        context_window=64000,
        parallel=1,
        extra_args=["--temp 0.2", "--top-p 0.9"],
        system_prompt=(
            "You are a coding agent working inside the user's repository.\n"
            "Rules: read files before editing them; make one tool call at a time and wait\n"
            "for its result; keep diffs minimal and match the existing code style; never\n"
            "invent file paths or APIs — verify with tools; state clearly when you are\n"
            "unsure instead of guessing."
        ),
    ),
    Preset(
        name="chat",
        description="General chat",
        extra_args=["--temp 0.7"],
    ),
]


# ---------------------------------------------------------------------------
# Managed server (owned local model)
# ---------------------------------------------------------------------------

class ManagedServer(BaseModel):
    name: str
    model_path: str
    model_draft: str | None = None
    port: int
    engine: str = "llama"
    context_window: int | None = None
    parallel: int = 1
    extra_args: list[str] = Field(default_factory=list)
    preset: str | None = None

    @property
    def effective_display_name(self) -> str:
        return self.name


# ---------------------------------------------------------------------------
# Remote server (distant, hand-edited)
# ---------------------------------------------------------------------------

class RemoteServer(BaseModel):
    name: str
    url: str
    family: str = "unknown"
    size: str = "unknown"
    context_length: int = 4096


# ---------------------------------------------------------------------------
# Root unified config
# ---------------------------------------------------------------------------

class UnifiedConfig(BaseModel):
    engine_defaults: dict[str, list[str]] = Field(default_factory=dict)
    managed_servers: list[ManagedServer] = Field(default_factory=list)
    remote_servers: list[RemoteServer] = Field(default_factory=list)
    presets: list[Preset] = Field(default_factory=list)
    aliases: list[ModelAlias] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_CONFIG_CACHE: dict[str, UnifiedConfig] = {}


def load_unified_config(path: str | Path | None = None) -> UnifiedConfig:
    """Load the unified config.yaml from project root.

    If the file doesn't exist or sections are missing/malformed, returns a
    config with safe defaults so the server can still start.
    """
    config_path = Path(path or DEFAULT_CONFIG_PATH)
    if not config_path.is_absolute():
        # Resolve relative to project root (two levels up from this file: app/ -> metallama/ -> project root).
        config_path = Path(__file__).resolve().parents[2] / config_path

    cache_key = str(config_path.resolve())
    if cache_key in _CONFIG_CACHE:
        return _CONFIG_CACHE[cache_key]

    if not config_path.exists():
        logger.info("Config file not found at %s, using defaults", config_path)
        config = UnifiedConfig()
        _CONFIG_CACHE[cache_key] = config
        return config

    try:
        with config_path.open() as fh:
            raw = yaml.safe_load(fh) or {}
    except Exception as exc:
        logger.warning("Failed to parse config file %s: %s — using defaults", config_path, exc)
        config = UnifiedConfig()
        _CONFIG_CACHE[cache_key] = config
        return config

    if not raw:
        logger.info("Config file %s is empty — using defaults", config_path)

    # Use `or {}` / `or []` to handle None from YAML (e.g. key present but empty)
    engine_defaults_raw = raw.get("engine_defaults") or {}
    engine_defaults: dict[str, list[str]] = {}
    for engine_name, defaults in engine_defaults_raw.items():
        engine_defaults[engine_name] = defaults if isinstance(defaults, list) else []

    managed = [ManagedServer(**entry) for entry in (raw.get("managed_servers") or []) if entry]
    remote = [RemoteServer(**entry) for entry in (raw.get("remote_servers") or []) if entry]
    presets_raw = [Preset(**entry) for entry in (raw.get("presets") or []) if entry]
    aliases_raw = [ModelAlias(**entry) for entry in (raw.get("aliases") or []) if entry]

    config = UnifiedConfig(
        engine_defaults=engine_defaults,
        managed_servers=managed,
        remote_servers=remote,
        presets=presets_raw,
        aliases=aliases_raw,
    )
    _CONFIG_CACHE[cache_key] = config
    return config


def find_preset(config: UnifiedConfig, name: str) -> Preset | None:
    """Look up a preset by name — config entries first, then built-in defaults."""
    for preset in config.presets:
        if preset.name == name:
            return preset
    for preset in DEFAULT_PRESETS:
        if preset.name == name:
            return preset
    return None


def clear_config_cache() -> None:
    """Clear the config cache (useful for regeneration workflows)."""
    _CONFIG_CACHE.clear()


def update_managed_server(server_id: str, updates: dict[str, Any], path: str | Path | None = None) -> None:
    """Update fields on a managed_server entry in config.yaml.

    Typical usage: update_managed_server("llamacpp-coding", {"context_window": 128000})
    """
    config = load_unified_config(path)
    for i, server in enumerate(config.managed_servers):
        if server.name == server_id:
            for key, value in updates.items():
                if hasattr(server, key):
                    setattr(server, key, value)
            config.managed_servers[i] = server
            save_unified_config(config, path)
            return
    raise ValueError(f"Managed server '{server_id}' not found in config")


def update_remote_server(server_id: str, updates: dict[str, Any], path: str | Path | None = None) -> None:
    """Update fields on a remote_server entry in config.yaml."""
    config = load_unified_config(path)
    for i, server in enumerate(config.remote_servers):
        if server.name == server_id:
            for key, value in updates.items():
                if hasattr(server, key):
                    setattr(server, key, value)
            config.remote_servers[i] = server
            save_unified_config(config, path)
            return
    raise ValueError(f"Remote server '{server_id}' not found in config")


def delete_managed_server(server_id: str, path: str | Path | None = None) -> None:
    """Remove a managed_server entry from config.yaml."""
    config = load_unified_config(path)
    before = len(config.managed_servers)
    config.managed_servers = [s for s in config.managed_servers if s.name != server_id]
    if len(config.managed_servers) == before:
        raise ValueError(f"Managed server '{server_id}' not found in config")
    save_unified_config(config, path)


def delete_remote_server(server_id: str, path: str | Path | None = None) -> None:
    """Remove a remote_server entry from config.yaml."""
    config = load_unified_config(path)
    before = len(config.remote_servers)
    config.remote_servers = [s for s in config.remote_servers if s.name != server_id]
    if len(config.remote_servers) == before:
        raise ValueError(f"Remote server '{server_id}' not found in config")
    save_unified_config(config, path)


def add_managed_server(data: dict[str, Any], path: str | Path | None = None) -> ManagedServer:
    """Add a new managed_server entry to config.yaml."""
    config = load_unified_config(path)
    if any(s.name == data.get("name") for s in config.managed_servers):
        raise ValueError(f"Managed server '{data.get('name')}' already exists")
    server = ManagedServer(**data)
    config.managed_servers.append(server)
    save_unified_config(config, path)
    return server


def add_remote_server(data: dict[str, Any], path: str | Path | None = None) -> RemoteServer:
    """Add a new remote_server entry to config.yaml."""
    config = load_unified_config(path)
    if any(s.name == data.get("name") for s in config.remote_servers):
        raise ValueError(f"Remote server '{data.get('name')}' already exists")
    server = RemoteServer(**data)
    config.remote_servers.append(server)
    save_unified_config(config, path)
    return server


def update_engine_defaults(engine: str, args: list[str], path: str | Path | None = None) -> None:
    """Replace the default CLI args for an engine in config.yaml."""
    config = load_unified_config(path)
    config.engine_defaults[engine] = args
    save_unified_config(config, path)


# ---------------------------------------------------------------------------
# Preset CRUD
# ---------------------------------------------------------------------------


def _get_preset(config: UnifiedConfig, name: str) -> Preset:
    """Look up a preset by name, raising ValueError if not found."""
    for p in config.presets:
        if p.name == name:
            return p
    raise ValueError(f"Preset '{name}' not found")


def add_preset(data: dict[str, Any], path: str | Path | None = None) -> Preset:
    """Add or update a preset in config.yaml (upsert by name)."""
    config = load_unified_config(path)
    existing_idx = next((i for i, p in enumerate(config.presets) if p.name == data.get("name")), None)
    preset = Preset(**data)
    if existing_idx is not None:
        config.presets[existing_idx] = preset
    else:
        config.presets.append(preset)
    save_unified_config(config, path)
    return preset


def update_preset(name: str, updates: dict[str, Any], path: str | Path | None = None) -> Preset:
    """Update fields on a preset entry in config.yaml."""
    config = load_unified_config(path)
    for i, preset in enumerate(config.presets):
        if preset.name == name:
            for key, value in updates.items():
                if hasattr(preset, key):
                    setattr(preset, key, value)
            config.presets[i] = preset
            save_unified_config(config, path)
            return preset
    raise ValueError(f"Preset '{name}' not found in config")


def delete_preset(name: str, path: str | Path | None = None) -> None:
    """Remove a preset from config.yaml. Raises ValueError if referenced."""
    config = load_unified_config(path)
    # Check references
    refs: list[str] = []
    for s in config.managed_servers:
        if s.preset == name:
            refs.append(f"server '{s.name}'")
    for a in config.aliases:
        if a.preset == name:
            refs.append(f"alias '{a.name}'")
    if refs:
        raise ValueError(f"Preset '{name}' is referenced by: {', '.join(refs)}")
    before = len(config.presets)
    config.presets = [p for p in config.presets if p.name != name]
    if len(config.presets) == before:
        raise ValueError(f"Preset '{name}' not found in config")
    save_unified_config(config, path)


# ---------------------------------------------------------------------------
# Alias CRUD
# ---------------------------------------------------------------------------


def add_alias(data: dict[str, Any], path: str | Path | None = None) -> ModelAlias:
    """Add or update an alias in config.yaml (upsert by name)."""
    config = load_unified_config(path)
    existing_idx = next((i for i, a in enumerate(config.aliases) if a.name == data.get("name")), None)
    alias = ModelAlias(**data)
    if existing_idx is not None:
        config.aliases[existing_idx] = alias
    else:
        config.aliases.append(alias)
    save_unified_config(config, path)
    return alias


def update_alias(name: str, updates: dict[str, Any], path: str | Path | None = None) -> ModelAlias:
    """Update fields on an alias entry in config.yaml."""
    config = load_unified_config(path)
    for i, alias in enumerate(config.aliases):
        if alias.name == name:
            for key, value in updates.items():
                if hasattr(alias, key):
                    setattr(alias, key, value)
            config.aliases[i] = alias
            save_unified_config(config, path)
            return alias
    raise ValueError(f"Alias '{name}' not found in config")


def delete_alias(name: str, path: str | Path | None = None) -> None:
    """Remove an alias from config.yaml."""
    config = load_unified_config(path)
    before = len(config.aliases)
    config.aliases = [a for a in config.aliases if a.name != name]
    if len(config.aliases) == before:
        raise ValueError(f"Alias '{name}' not found in config")
    save_unified_config(config, path)


def _q(value: str) -> str:
    """Return a double-quoted YAML scalar with backslashes and quotes escaped."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _yaml_str_value(value: Any) -> str:
    """Format a single value as a YAML scalar (quote strings that need it)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        needs_quote = value.lower() in ("true", "false", "null", "on", "off", "yes", "no")
        try:
            int(value)
            needs_quote = True
        except ValueError:
            pass
        try:
            float(value)
            needs_quote = True
        except ValueError:
            pass
        if any(c in value for c in (":", "#", "{", "}", "[", "]", ",", "&", "*", "?", "|", "-", "<", ">", "=", "!", "%", "@", "`")):
            needs_quote = True
        if needs_quote:
            return f"'{value}'"
        return value
    return str(value)


def save_unified_config(config: UnifiedConfig, path: str | Path | None = None) -> None:
    """Save the unified config back to YAML with comments preserved.

    Uses a template-based writer instead of yaml.dump() so that human-edited
    comments and section headers are preserved across saves.
    """
    config_path = Path(path or DEFAULT_CONFIG_PATH)
    if not config_path.is_absolute():
        config_path = Path(__file__).resolve().parents[2] / config_path

    lines: list[str] = []
    lines.append("# Metallama Unified Configuration")
    lines.append("# =================================")
    lines.append("# This file is the single source of truth for all server configurations.")
    lines.append("#")
    lines.append("# Sections:")
    lines.append("#   engine_defaults  - Default parameters for llama.cpp servers (machine-managed)")
    lines.append("#   managed_servers  - Owned local models (machine-generated, can be regenerated)")
    lines.append("#   remote_servers   - Distant servers (hand-edited by humans)")
    lines.append("#")
    lines.append("# Machine-managed sections may contain auto-generated comments.")
    lines.append("# Remote servers section is safe for manual editing.")
    lines.append("")

    # --- engine_defaults ---
    lines.append("# ---------------------------------------------------------------------------")
    lines.append("# Engine Defaults (Machine-Managed)")
    lines.append("# Default CLI args prepended to every server launch for this engine.")
    lines.append("# Last flag wins when merged with per-server args.")
    lines.append("# ---------------------------------------------------------------------------")
    lines.append("engine_defaults:")
    for engine_name, args in config.engine_defaults.items():
        lines.append(f"  {engine_name}:")
        if args:
            for arg in args:
                lines.append(f"    - {arg}")
        else:
            lines.append("    []")
    lines.append("")

    # --- managed_servers ---
    lines.append("# ---------------------------------------------------------------------------")
    lines.append("# Managed Servers (Machine-Generated)")
    lines.append("# Local models owned by this project. Configuration is generated/managed")
    lines.append("# by the application. Manual edits may be overwritten on regeneration.")
    lines.append("# ---------------------------------------------------------------------------")
    lines.append("managed_servers:")
    for server in config.managed_servers:
        lines.append(f"  - name: {_q(server.name)}")
        lines.append(f"    model_path: {_q(server.model_path)}")
        if server.model_draft:
            lines.append(f"    model_draft: {_q(server.model_draft)}")
        lines.append(f"    port: {server.port}")
        if server.engine != "llama":
            lines.append(f"    engine: {_q(server.engine)}")
        lines.append(f"    context_window: {'null' if server.context_window is None else server.context_window}")
        lines.append(f"    parallel: {server.parallel}")
        if server.extra_args:
            lines.append("    extra_args:")
            for arg in server.extra_args:
                lines.append(f"      - {arg}")
        else:
            lines.append("    extra_args: []")
        if server.preset:
            lines.append(f"    preset: {_q(server.preset)}")
    lines.append("")

    # --- remote_servers ---
    lines.append("# ---------------------------------------------------------------------------")
    lines.append("# Remote Servers (Hand-Edited)")
    lines.append("# Distant servers not owned by this project. Safe for manual editing.")
    lines.append("# These are read-only from the application's perspective.")
    lines.append("# ---------------------------------------------------------------------------")
    lines.append("remote_servers:")
    for server in config.remote_servers:
        lines.append(f"  - name: {_q(server.name)}")
        lines.append(f"    url: {_q(server.url)}")
        lines.append(f"    family: {_q(server.family)}")
        lines.append(f"    size: {_q(server.size)}")
        lines.append(f"    context_length: {server.context_length}")
    lines.append("")

    # --- presets ---
    lines.append("# ---------------------------------------------------------------------------")
    lines.append("# Presets (Named launch params + system prompts)")
    lines.append("# Presets can be applied to managed servers to set launch parameters")
    lines.append("# and system prompts. Default presets exist; config presets override them.")
    lines.append("# ---------------------------------------------------------------------------")
    lines.append("presets:")
    for preset in config.presets:
        lines.append(f"  - name: {_q(preset.name)}")
        if preset.description:
            lines.append(f"    description: {_q(preset.description)}")
        lines.append(f"    context_window: {'null' if preset.context_window is None else preset.context_window}")
        lines.append(f"    parallel: {'null' if preset.parallel is None else preset.parallel}")
        if preset.extra_args:
            lines.append("    extra_args:")
            for arg in preset.extra_args:
                lines.append(f"      - {arg}")
        else:
            lines.append("    extra_args: []")
        if preset.system_prompt is not None:
            # Multiline: emit as YAML block scalar (|-) with correct indentation (strip trailing newline)
            if "\n" in preset.system_prompt:
                lines.append("    system_prompt: |-")
                for line in preset.system_prompt.split("\n"):
                    lines.append(f"      {line}")
            else:
                lines.append(f"    system_prompt: {_q(preset.system_prompt)}")
        else:
            lines.append("    system_prompt: null")
    lines.append("")

    # --- aliases ---
    lines.append("# ---------------------------------------------------------------------------")
    lines.append("# Model Aliases (Config-file only in v1)")
    lines.append("# Aliases expose a server under a different model name with optional preset.")
    lines.append("# ---------------------------------------------------------------------------")
    lines.append("aliases:")
    for alias in config.aliases:
        lines.append(f"  - name: {_q(alias.name)}")
        lines.append(f"    server: {_q(alias.server)}")
        preset_val = "null" if alias.preset is None else _q(alias.preset)
        lines.append(f"    preset: {preset_val}")
    lines.append("")

    with config_path.open("w") as fh:
        fh.write("\n".join(lines))

    clear_config_cache()
