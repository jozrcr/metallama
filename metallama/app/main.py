from __future__ import annotations

import asyncio
import os
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


@dataclass(frozen=True)
class ModelProfile:
    id: str
    display_name: str
    modality: str
    use_case: str
    family: str
    size: str
    description: str
    model_path: str | Path
    port: int
    extra_args: list[str]


@dataclass
class ProcessState:
    process: subprocess.Popen[str]
    started_at: float
    command: list[str]


class ConfigUpdate(BaseModel):
    llamacpp_binary: str = Field(min_length=1)
    base_url: str | None = None

MODELS_DIR = Path("/envs/local/llm/models/")
APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"

MODEL_PROFILES: dict[str, ModelProfile] = {
    "qwen35-27b-code": ModelProfile(
        id="qwen35-27b-code",
        display_name="Qwen 3.5 27B",
        modality="text",
        use_case="code",
        family="Qwen 3.5",
        size="27B",
        description="Primary coding model for chat and generation tasks.",
        model_path=MODELS_DIR / "Qwen3.5-27B-Q8_0.gguf",
        port=8011,
        extra_args=[
            "--ctx-size 229376",
            "--threads 16",
            "--n-gpu-layers 999",
            "--temp 1.0",
            "--top-p 0.95",
            "--top-k 20",
            "--min-p 0.00",
            "--presence_penalty 1.5",
            "--repeat-penalty 1.0",
        ],
    ),
    "qwen25-omni-7b-audio": ModelProfile(
        id="qwen25-omni-7b-audio",
        display_name="Qwen 2.5 Omni 7B",
        modality="audio",
        use_case="speech",
        family="Qwen 2.5 Omni",
        size="7B",
        description="Audio-capable model for speech and multimodal workflows.",
        model_path=MODELS_DIR / "Qwen2.5-Omni-7B.gguf",
        port=8012,
        extra_args=[
            "--ctx-size 16384",
            "--threads 8",
            "--n-gpu-layers 999",
        ],
    ),
}

app = FastAPI(title="metallama")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


runtime_processes: dict[str, ProcessState] = {}
model_locks: dict[str, asyncio.Lock] = {key: asyncio.Lock() for key in MODEL_PROFILES}

CONFIG: dict[str, str] = {
    "llamacpp_binary": os.getenv("METALLAMA_LLAMACPP_BINARY", "/local_home/debian/llm/llama.cpp/build/bin/llama-server"),
    "base_url": os.getenv("METALLAMA_BASE_URL", "http://gpu4.hygeos.com"),
}


def _is_alive(proc: subprocess.Popen[str]) -> bool:
    return proc.poll() is None


def _cleanup_dead(model_id: str) -> None:
    state = runtime_processes.get(model_id)
    if state and not _is_alive(state.process):
        runtime_processes.pop(model_id, None)


def _build_command(profile: ModelProfile) -> list[str]:
    binary = CONFIG["llamacpp_binary"].strip()
    if not binary:
        raise HTTPException(status_code=400, detail="llamacpp_binary is empty")

    binary_path = Path(binary)
    if binary_path.is_absolute() and not binary_path.exists():
        raise HTTPException(status_code=400, detail=f"Binary does not exist: {binary}")

    model_path = Path(profile.model_path)
    if not model_path.exists():
        raise HTTPException(status_code=400, detail=f"Model file not found: {profile.model_path}")

    # Accept both extra arg styles:
    # - ["--ctx-size", "8192"]
    # - ["--ctx-size 8192", "--threads 8"]
    normalized_extra_args: list[str] = []
    for arg in profile.extra_args:
        parts = shlex.split(arg)
        normalized_extra_args.extend(parts if parts else [arg])

    return [
        binary,
        "--model",
        str(model_path),
        "--host",
        "0.0.0.0",
        "--port",
        str(profile.port),
        *normalized_extra_args,
    ]


def _status_for(model_id: str) -> str:
    _cleanup_dead(model_id)
    state = runtime_processes.get(model_id)
    if not state:
        return "stopped"
    return "running" if _is_alive(state.process) else "stopped"


def _model_payload(profile: ModelProfile) -> dict[str, Any]:
    status = _status_for(profile.id)
    state = runtime_processes.get(profile.id)
    return {
        "id": profile.id,
        "display_name": profile.display_name,
        "modality": profile.modality,
        "use_case": profile.use_case,
        "family": profile.family,
        "size": profile.size,
        "description": profile.description,
        "port": profile.port,
        "url": f"{CONFIG['base_url']}:{profile.port}",
        "status": status,
        "pid": state.process.pid if state and status == "running" else None,
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/config")
def get_config() -> dict[str, str]:
    return dict(CONFIG)


@app.post("/api/config")
def update_config(payload: ConfigUpdate) -> dict[str, str]:
    CONFIG["llamacpp_binary"] = payload.llamacpp_binary.strip()
    if payload.base_url is not None:
        CONFIG["base_url"] = payload.base_url.strip() or CONFIG["base_url"]
    return dict(CONFIG)


@app.get("/api/models")
def list_models() -> dict[str, Any]:
    return {"models": [_model_payload(profile) for profile in MODEL_PROFILES.values()]}


@app.post("/api/models/{model_id}/start")
async def start_model(model_id: str) -> dict[str, Any]:
    profile = MODEL_PROFILES.get(model_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Unknown model id")

    async with model_locks[model_id]:
        _cleanup_dead(model_id)
        existing = runtime_processes.get(model_id)
        if existing and _is_alive(existing.process):
            raise HTTPException(status_code=409, detail="Already running")

        command = _build_command(profile)
        try:
            proc = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=f"Binary not found: {command[0]}") from exc

        runtime_processes[model_id] = ProcessState(
            process=proc,
            started_at=time.time(),
            command=command,
        )

    return {"ok": True, "model": _model_payload(profile)}


@app.post("/api/models/{model_id}/stop")
async def stop_model(model_id: str) -> dict[str, Any]:
    profile = MODEL_PROFILES.get(model_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Unknown model id")

    async with model_locks[model_id]:
        _cleanup_dead(model_id)
        state = runtime_processes.get(model_id)
        if not state:
            return {"ok": True, "model": _model_payload(profile)}

        proc = state.process
        if _is_alive(proc):
            proc.terminate()
            for _ in range(20):
                if not _is_alive(proc):
                    break
                await asyncio.sleep(0.25)
            if _is_alive(proc):
                proc.kill()

        runtime_processes.pop(model_id, None)

    return {"ok": True, "model": _model_payload(profile)}


@app.get("/api/models/{model_id}/status")
def model_status(model_id: str) -> dict[str, Any]:
    profile = MODEL_PROFILES.get(model_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Unknown model id")
    return _model_payload(profile)


@app.on_event("shutdown")
def stop_all_on_shutdown() -> None:
    for model_id, state in list(runtime_processes.items()):
        proc = state.process
        if _is_alive(proc):
            proc.send_signal(signal.SIGTERM)
        runtime_processes.pop(model_id, None)


@app.get("/api/models/{model_id}/command")
def model_command_preview(model_id: str) -> dict[str, str]:
    profile = MODEL_PROFILES.get(model_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Unknown model id")
    command = _build_command(profile)
    return {"command": shlex.join(command)}
