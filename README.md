# metallama

Minimal llama.cpp meta server:
- Backend: FastAPI
- Frontend: vanilla HTML/CSS/JS
- One process maximum per model card

## Quick start

```bash
cd /local_home/debian/llm/metallama/
source .venv/bin/activate
pip install -e .
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Open: http://127.0.0.1:8000

## Configure binary path

Use the web UI Runtime Config form:
- `llama.cpp binary path`: absolute path or command in `PATH` (for example `llama-server`)
- `base URL`: value used to display model endpoint URLs

You can also set environment variables before launch:
- `METALLAMA_LLAMACPP_BINARY`
- `METALLAMA_BASE_URL`

## Hardcoded model profiles

Model profiles live in `app/main.py` in `MODEL_PROFILES`.

Current cards:
- `qwen35-27b-code` -> `LLM / code :: Qwen 3.5 27B`
- `qwen25-omni-7b-audio` -> `Audio :: Qwen 2.5 Omni 7B`

Each profile has:
- `model_path`
- `port`
- hardcoded llama.cpp args (`extra_args`)

## API

- `GET /api/config`
- `POST /api/config`
- `GET /api/models`
- `GET /api/models/{id}/status`
- `POST /api/models/{id}/start`
- `POST /api/models/{id}/stop`
- `GET /api/models/{id}/command`

## Single-instance rule

For each model card:
- Start returns `409` if already running
- Stop is idempotent
- A per-model async lock prevents concurrent start/stop race conditions
