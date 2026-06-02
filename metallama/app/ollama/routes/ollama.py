from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from ..probe import probe_one, _DEFAULT_CONTEXT_LENGTH
from ..registry import get_subserver, get_all_subservers
from ..schemas import OllamaChatRequest, OllamaGenerateRequest, OllamaShowRequest

router = APIRouter()

_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=5.0)
_HEALTH_TIMEOUT = httpx.Timeout(1.0)


def _digest(name: str) -> str:
    return "sha256:" + hashlib.md5(name.encode()).hexdigest()  # noqa: S324 – non-cryptographic identifier


def _now() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# GET /api/tags
# ---------------------------------------------------------------------------


@router.get("/api/tags")
async def list_tags() -> JSONResponse:
    models = []
    async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT) as client:
        for srv in get_all_subservers():
            try:
                resp = await client.get(f"{srv.url}/health")
                if resp.status_code != 200:
                    continue
            except (httpx.ConnectError, httpx.TimeoutException):
                continue
            # Server is up but probe may have missed it at startup — re-probe lazily
            if srv.context_length == _DEFAULT_CONTEXT_LENGTH:
                await probe_one(srv, client)
            arch = srv.upstream_meta.get("general.architecture", srv.family)
            quant = srv.upstream_meta.get("quantization", "unknown")
            models.append(
                {
                    "name": srv.name,
                    "model": srv.name,
                    "modified_at": "2025-01-01T00:00:00Z",
                    "size": srv.size,
                    "digest": _digest(srv.name),
                    "details": {
                        "format": "gguf",
                        "family": arch,
                        "families": [arch],
                        "parameter_size": srv.parameter_size,
                        "quantization_level": quant,
                        "context_length": srv.context_length,
                    },
                }
            )
    return JSONResponse({"models": models})


# ---------------------------------------------------------------------------
# GET /api/ps
# ---------------------------------------------------------------------------


@router.get("/api/ps")
async def list_running() -> JSONResponse:
    running = []
    async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT) as client:
        for srv in get_all_subservers():
            try:
                resp = await client.get(f"{srv.url}/health")
                if resp.status_code == 200:
                    running.append(
                        {
                            "name": srv.name,
                            "model": srv.name,
                            "size": srv.size,
                            "digest": _digest(srv.name),
                            "expires_at": None,
                            "size_vram": 0,
                            "details": {
                                "format": "gguf",
                                "family": srv.family,
                                "parameter_size": srv.parameter_size,
                            },
                        }
                    )
            except (httpx.ConnectError, httpx.TimeoutException):
                pass
    return JSONResponse({"models": running})


# ---------------------------------------------------------------------------
# GET /api/version
# ---------------------------------------------------------------------------


@router.get("/api/version")
async def version() -> JSONResponse:
    return JSONResponse({"version": "0.6.4"})


# ---------------------------------------------------------------------------
# POST /api/show
# ---------------------------------------------------------------------------


@router.post("/api/show")
async def show(req: OllamaShowRequest) -> JSONResponse:
    srv = get_subserver(req.model_name)
    arch = srv.upstream_meta.get("general.architecture", srv.family)
    n_embd = srv.upstream_meta.get("n_embd", 4096)
    quant = srv.upstream_meta.get("quantization", "unknown")
    model_info = {
        "general.architecture": arch,
        "general.parameter_count": srv.parameter_size,
        f"{arch}.context_length": srv.context_length,
        f"{arch}.embedding_length": n_embd,
    }

    return JSONResponse(
        {
            "model": srv.name,
            "details": {
                "parent_model": "",
                "format": "gguf",
                "family": arch,
                "families": [arch],
                "parameter_size": srv.parameter_size,
                "quantization_level": quant,
                "context_length": srv.context_length,
            },
            "context_length": srv.context_length,
            "model_info": model_info,
            "modelinfo": model_info,
            "parameters": f"num_ctx {srv.context_length}\nstop \"<|im_end|>\"",
            "capabilities": ["completion", "tools"],
        }
    )


# ---------------------------------------------------------------------------
# POST /api/chat
# ---------------------------------------------------------------------------


async def _stream_chat(model: str, chunks: AsyncIterator[bytes]) -> AsyncIterator[str]:
    """Translate OpenAI SSE stream → Ollama NDJSON stream."""
    async for raw in chunks:
        for line in raw.decode().splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if payload == "[DONE]":
                yield json.dumps({"model": model, "created_at": _now(), "message": {"role": "assistant", "content": ""}, "done": True}) + "\n"
                return
            try:
                data: dict[str, Any] = json.loads(payload)
            except json.JSONDecodeError:
                continue
            delta = data.get("choices", [{}])[0].get("delta", {})
            content = delta.get("content", "")
            if content:
                yield json.dumps({
                    "model": model,
                    "created_at": _now(),
                    "message": {"role": "assistant", "content": content},
                    "done": False,
                }) + "\n"


@router.post("/api/chat", response_model=None)
async def chat(req: OllamaChatRequest) -> StreamingResponse | JSONResponse:
    srv = get_subserver(req.model)
    payload = {
        "model": req.model,
        "messages": [m.model_dump() for m in req.messages],
        "stream": req.stream,
    }
    if req.options:
        payload.update(req.options)

    if req.stream:
        async def generate() -> AsyncIterator[bytes]:
            try:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                    async with client.stream("POST", f"{srv.url}/v1/chat/completions", json=payload) as resp:
                        if resp.status_code != 200:
                            yield (json.dumps({"error": "upstream error"}) + "\n").encode()
                            return
                        async for chunk in _stream_chat(req.model, resp.aiter_bytes()):
                            yield chunk.encode()
            except httpx.ConnectError:
                yield (json.dumps({"error": "upstream unreachable"}) + "\n").encode()
            except httpx.TimeoutException:
                yield (json.dumps({"error": "upstream timeout"}) + "\n").encode()

        return StreamingResponse(generate(), media_type="application/x-ndjson")

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(f"{srv.url}/v1/chat/completions", json=payload)
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail={"error": "upstream unreachable"})
    except httpx.TimeoutException:
        raise HTTPException(status_code=502, detail={"error": "upstream timeout"})

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail={"error": "upstream error"})

    data = resp.json()
    choice = data.get("choices", [{}])[0]
    content = choice.get("message", {}).get("content", "")
    return JSONResponse({
        "model": req.model,
        "created_at": _now(),
        "message": {"role": "assistant", "content": content},
        "done": True,
    })


# ---------------------------------------------------------------------------
# POST /api/generate
# ---------------------------------------------------------------------------


async def _stream_generate(model: str, chunks: AsyncIterator[bytes]) -> AsyncIterator[str]:
    """Translate OpenAI SSE stream → Ollama generate NDJSON stream."""
    async for raw in chunks:
        for line in raw.decode().splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if payload == "[DONE]":
                yield json.dumps({"model": model, "created_at": _now(), "response": "", "done": True}) + "\n"
                return
            try:
                data: dict[str, Any] = json.loads(payload)
            except json.JSONDecodeError:
                continue
            text = data.get("choices", [{}])[0].get("text", "")
            if text:
                yield json.dumps({
                    "model": model,
                    "created_at": _now(),
                    "response": text,
                    "done": False,
                }) + "\n"


@router.post("/api/generate", response_model=None)
async def generate_endpoint(req: OllamaGenerateRequest) -> StreamingResponse | JSONResponse:
    srv = get_subserver(req.model)
    payload = {
        "model": req.model,
        "prompt": req.prompt,
        "stream": req.stream,
    }
    if req.options:
        payload.update(req.options)

    if req.stream:
        async def generate() -> AsyncIterator[bytes]:
            try:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                    async with client.stream("POST", f"{srv.url}/v1/completions", json=payload) as resp:
                        if resp.status_code != 200:
                            yield (json.dumps({"error": "upstream error"}) + "\n").encode()
                            return
                        async for chunk in _stream_generate(req.model, resp.aiter_bytes()):
                            yield chunk.encode()
            except httpx.ConnectError:
                yield (json.dumps({"error": "upstream unreachable"}) + "\n").encode()
            except httpx.TimeoutException:
                yield (json.dumps({"error": "upstream timeout"}) + "\n").encode()

        return StreamingResponse(generate(), media_type="application/x-ndjson")

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(f"{srv.url}/v1/completions", json=payload)
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail={"error": "upstream unreachable"})
    except httpx.TimeoutException:
        raise HTTPException(status_code=502, detail={"error": "upstream timeout"})

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail={"error": "upstream error"})

    data = resp.json()
    text = data.get("choices", [{}])[0].get("text", "")
    return JSONResponse({
        "model": req.model,
        "created_at": _now(),
        "response": text,
        "done": True,
    })


# ---------------------------------------------------------------------------
# Stubbed management endpoints
# ---------------------------------------------------------------------------

_NOT_SUPPORTED = JSONResponse({"error": "not supported"}, status_code=400)


@router.post("/api/pull")
async def pull() -> JSONResponse:
    return _NOT_SUPPORTED


@router.post("/api/push")
async def push() -> JSONResponse:
    return _NOT_SUPPORTED


@router.post("/api/copy")
async def copy() -> JSONResponse:
    return _NOT_SUPPORTED


@router.post("/api/delete")
async def delete() -> JSONResponse:
    return _NOT_SUPPORTED


@router.post("/api/create")
async def create() -> JSONResponse:
    return _NOT_SUPPORTED
