# SPEC: Presets, Gateway Prompt Injection, Model Aliases

**Branch:** `feat/agentic-presets` — work here, never on `main`.
**Implementer:** Qwen. **Reviewer:** Claude. Follow this spec exactly; if something
is ambiguous or impossible, STOP and write the question in `IMPLEMENTATION_NOTES.md`
instead of improvising.

## Goal

Let a user define named **presets** (launch params + a system prompt), apply them to
servers, and expose **aliases** so one running server can appear under several model
names with different behavior. The gateway injects the preset's system prompt when a
conversation doesn't already have one — so every client (VS Code, Qwen Code, Cline,
curl) gets the same instructions without per-client setup.

## 1. Data model (`metallama/app/unified_config.py`)

New Pydantic models:

```python
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
```

- `UnifiedConfig` gains `presets: list[Preset]` and `aliases: list[ModelAlias]`.
- `ManagedServer` gains `preset: str | None = None`.
- Loader: parse new sections with the same defensive style as existing ones
  (`raw.get("presets") or []`).
- Writer (`save_unified_config`): emit the new sections in the same hand-rolled
  style as the existing ones. `system_prompt` may be multiline — emit it as a
  YAML block scalar (`system_prompt: |`) with correct indentation, or if it is
  single-line, as a quoted string. **Round-trip test required** (save → load →
  identical values, including multiline prompts with quotes in them).
- CRUD helpers mirroring the existing ones: `add_preset`, `update_preset`,
  `delete_preset` (raise ValueError if referenced by a server or alias),
  `add_alias`, `update_alias`, `delete_alias`.

## 2. Launch-param resolution (`metallama/app/runtime.py`)

In `get_profile_with_config()`: if the server entry has a `preset`, resolve it and
merge with precedence **server field > preset field > existing default**:

- `context_window`: server's explicit value wins; else preset's; else as today.
- `parallel`: same rule. (Note: `ManagedServer.parallel` defaults to `1`, which is
  indistinguishable from "unset" — acceptable for v1; document it.)
- `extra_args`: concatenation `engine_defaults + preset.extra_args + server.extra_args`
  (later flags win in llama.cpp). Implement by extending the two places that build
  `extra_args` in `build_command` / `build_command_preview` — add preset args between
  engine defaults and server args. Both functions MUST behave identically.

If `server.preset` names a preset that doesn't exist: ignore it (log a warning),
do not crash.

## 3. Gateway (`metallama/app/ollama/`)

### 3.1 Registry aliases (`registry.py`)

- `rebuild_registry()` additionally registers each `ModelAlias` as a routable name:
  it resolves `alias.server` to that server's URL (managed → `http://127.0.0.1:{port}`,
  remote → its URL). Store alias entries so lookup by alias name returns the same
  `SubserverConfig` shape plus the alias's preset name (add an optional
  `preset: str | None = None` field to `SubserverConfig`).
- A server whose config names a `preset` gets that preset on its own registry entry
  too (aliases override it for their own name).
- `/api/tags` and `/v1/models` must list aliases as separate models (health-checked
  via the underlying server URL).

### 3.2 System-prompt injection (`routes/ollama.py` and `routes/openai.py`)

Shared helper (put it in `registry.py` or a small new module, import from both):

```python
def resolve_system_prompt(model_name: str) -> str | None
```

Returns the system prompt of the preset attached to the alias/server, or None.
Look the preset up from `load_unified_config()` at request time (config cache makes
this cheap; edits apply without restart).

Injection rule, applied in `/api/chat` (Ollama route) and `/v1/chat/completions`
(OpenAI route), for both stream and non-stream:

- If `resolve_system_prompt(model)` is None → forward unchanged.
- If the request's `messages` already contain a message with `role == "system"`
  → forward unchanged (client instructions win).
- Otherwise prepend `{"role": "system", "content": prompt}` to `messages`.

The OpenAI route currently forwards the raw body; it already parses `body` as JSON —
modify `body["messages"]` before forwarding. Do NOT alter anything else in the body.

`/v1/completions`, `/api/generate`, `/v1/embeddings`: no injection.

## 4. API endpoints (`metallama/app/main.py`)

All mutating endpoints use `Depends(admin_guard)` like their neighbors.

- `GET  /api/presets` → `{"presets": [...]}` (full objects)
- `POST /api/presets` → create or update by `name` (body = Preset fields; validate:
  non-empty name, extra_args list of strings, context_window/parallel positive ints
  or null). Returns the saved preset.
- `DELETE /api/presets/{name}` → 409 with a clear message if any server or alias
  references it; 404 if unknown.
- `GET  /api/aliases`, `POST /api/aliases`, `DELETE /api/aliases/{name}` — same
  pattern; POST validates that `server` exists (managed or remote) and `preset`,
  if given, exists.
- Every mutation calls `reload_model_profiles()` (if it touches servers) and
  `rebuild_ollama_registry()` so the gateway picks changes up immediately.
- `POST /api/models/create` and `/api/models/{name}/config` accept an optional
  `preset` string field (validated: must exist or be empty/null).

## 5. Seed presets

In `load_unified_config()`: **do not** silently write files. Instead, expose
`DEFAULT_PRESETS` (a list of two `Preset` objects) from `unified_config.py`:

- `agentic-coding`: description "Tool-using coding agent", context_window 64000,
  parallel 1, extra_args `["--temp 0.2", "--top-p 0.9"]`, system_prompt (verbatim):

  ```
  You are a coding agent working inside the user's repository.
  Rules: read files before editing them; make one tool call at a time and wait
  for its result; keep diffs minimal and match the existing code style; never
  invent file paths or APIs — verify with tools; state clearly when you are
  unsure instead of guessing.
  ```

- `chat`: description "General chat", extra_args `["--temp 0.7"]`, no ctx/parallel,
  no system_prompt.

`GET /api/presets` returns `DEFAULT_PRESETS` entries merged in ONLY when no preset
with the same name exists in config (config wins). Saving a preset writes it to
config.yaml as normal.

## 6. UI (vanilla JS, match existing patterns exactly)

1. **Create/Edit server modal**: a "Preset" `<select>` (options: none + all presets)
   above Context Window. Choosing a preset in *create* mode prefills ctx/parallel
   inputs from the preset (user can still edit; edited values are saved on the
   server and win over the preset). The chosen preset name is sent in the payload.
2. **Presets modal**: new button "✦ Presets" next to "⚙ Defaults" (admin-only).
   Lists presets (name, description, param summary); create/edit form with fields:
   name, description, context window, parallel, extra args (textarea, one per line),
   system prompt (textarea). Delete button with confirm; surface the 409 message
   if the preset is in use. Reuse existing modal markup/CSS patterns (`.modal-overlay`,
   `.form-group`, etc.) — no new design language.
3. **Server card**: if the server has a preset, show an info chip `✦ <preset>`.
4. **Connect modal**: the model `<select>` must also list aliases (source them from
   `/api/aliases` or extend `/api/models` — implementer's choice, note it).

No alias-management UI in v1 (config-file only) beyond the Connect listing.

## 7. Constraints — read carefully

- Do NOT touch: download code (`hf.py`), logs machinery, GPU/gguf modules,
  the parallel-download block logic, styles unrelated to the new UI.
- Do NOT reformat existing code or reorder imports in files you touch.
- Do NOT add dependencies.
- Keep `async` correctness: anything calling `model_payload`/`status_for` must await.
- Every endpoint you add or change must be exercised with a real `curl` against a
  running instance before you commit. Use an isolated config:
  `METALLAMA_CONFIG_FILE=/tmp/spec-test.yaml METALLAMA_ADMIN_PASS_HASH= uvicorn metallama.app.main:app --port 8765`
- Commit in logical units with clear messages. Do not commit anything untested.
  Record anything you couldn't test and every deviation from this spec in
  `IMPLEMENTATION_NOTES.md`.

## 8. Acceptance criteria (reviewer will run these verbatim)

1. Round-trip: create a preset with a 3-line system prompt containing `"` and `:`
   via `POST /api/presets`; restart the app; `GET /api/presets` returns it intact.
2. Create a server with `preset: agentic-coding` and no explicit ctx → its
   `/api/models/{name}/command` shows `--ctx-size 64000` and `--temp 0.2`; give the
   server explicit `context_window: 8000` → command shows `--ctx-size 8000` while
   `--temp 0.2` remains.
3. `POST /ollama/api/chat` (non-stream) to that server with no system message →
   upstream receives the preset's system prompt prepended (verify with a mock or
   the fake llama-server + logs). Same request WITH a client system message →
   forwarded unchanged.
4. Same injection behavior on `/ollama/v1/chat/completions` (stream and non-stream);
   the rest of the body is byte-identical apart from `messages`.
5. Alias `qwen-coder` → server X with preset P: appears in `/ollama/api/tags` and
   `/ollama/v1/models`; chatting with `model: "qwen-coder"` routes to X and injects
   P's prompt; chatting with X's own name does not use P (unless X itself has P).
6. `DELETE /api/presets/agentic-coding` while referenced → 409 + message; after
   removing the reference → 200 and it's gone from config.yaml.
7. Full existing regression still passes: server create/start (loading strip data,
   `/health`-based status)/stop, crash banner on a failing model, port-conflict 409,
   `/api/library`, discard guard, `/ollama/api/tags`.
