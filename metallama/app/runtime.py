from __future__ import annotations

import asyncio
import shlex
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from .config import Config
from .models import ModelProfile, ProcessState
from .profiles import MODEL_PROFILES


runtime_processes: dict[str, ProcessState] = {}
model_locks: dict[str, asyncio.Lock] = {key: asyncio.Lock() for key in MODEL_PROFILES}


def is_alive(proc: subprocess.Popen[str]) -> bool:
    return proc.poll() is None


def cleanup_dead(model_id: str) -> None:
    state = runtime_processes.get(model_id)
    if state and not is_alive(state.process):
        runtime_processes.pop(model_id, None)


def is_port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def is_whisper_ready(port: int, timeout: float = 0.5) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def resolve_mineru_binary() -> str:
    venv_path = Config.EXECUTABLE_MINERU_VENV.strip()
    if not venv_path:
        raise HTTPException(status_code=400, detail="METALLAMA_MINERU_VENV is not configured")

    binary = Path(venv_path) / "bin" / "mineru-api"
    if not binary.exists():
        raise HTTPException(status_code=400, detail=f"MinerU executable not found: {binary}")

    return str(binary)


def build_command(profile: ModelProfile) -> list[str]:
    if profile.engine == "whisper":
        binary = Config.EXECUTABLE_WHISPER
    elif profile.engine == "mineru":
        binary = resolve_mineru_binary()
    else:
        binary = Config.EXECUTABLE_LLAMA

    if not binary:
        raise HTTPException(status_code=400, detail=f"{profile.engine} binary is empty")

    binary_path = Path(binary)
    if binary_path.is_absolute() and not binary_path.exists():
        raise HTTPException(status_code=400, detail=f"Binary does not exist: {binary}")

    normalized_extra_args: list[str] = []
    for arg in profile.extra_args:
        parts = shlex.split(arg)
        normalized_extra_args.extend(parts if parts else [arg])

    if profile.engine == "mineru":
        return [
            str(binary),
            "--host",
            "0.0.0.0",
            "--port",
            str(profile.port),
            *normalized_extra_args,
        ]

    model_path = Path(profile.model_path)
    if not model_path.exists():
        raise HTTPException(status_code=400, detail=f"Model file not found: {profile.model_path}")

    return [
        str(binary),
        "--model",
        str(model_path),
        "--host",
        "0.0.0.0",
        "--port",
        str(profile.port),
        *normalized_extra_args,
    ]


def status_for(profile: ModelProfile) -> str:
    cleanup_dead(profile.id)
    state = runtime_processes.get(profile.id)

    if profile.engine == "mineru":
        return "running" if is_port_open("127.0.0.1", profile.port) else "stopped"

    if not state:
        return "stopped"
    if not is_alive(state.process):
        return "stopped"

    if profile.engine == "whisper":
        return "running" if is_whisper_ready(profile.port) else "starting"

    return "running" if is_port_open("127.0.0.1", profile.port) else "starting"


def model_payload(profile: ModelProfile) -> dict[str, Any]:
    status = status_for(profile)
    state = runtime_processes.get(profile.id)
    return {
        "id": profile.id,
        "display_name": profile.display_name,
        "engine": profile.engine,
        "service": profile.service,
        "family": profile.family,
        "size": profile.size,
        "description": profile.description,
        "port": profile.port,
        "url": f"{Config.BASE_URL}:{profile.port}",
        "status": status,
        "pid": state.process.pid if state and status == "running" else None,
    }
