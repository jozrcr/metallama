# Test suite

Run: `.venv/bin/pytest -q` — 31 tests, ~0.3s, no network, no GPU, no running
app. Every test targets something that has actually broken in this project's
history. Isolated paths only (`tmp_path`); the repo-root `config.yaml` is
never touched.

## test_unified_config.py (7 tests) — config persistence

The hand-rolled YAML writer corrupted the whole config on a `"` once
(data-loss class: parse failure silently presents an empty config).

- `test_round_trip_config` — full save→load cycle of a config containing an
  engine default, a managed server referencing a preset, a preset whose
  description holds `"quotes"`, colons and a backslash plus a multiline
  system prompt, and an alias. Asserts every field survives byte-identically
  (including no trailing newline appended to block-scalar prompts).
- `test_find_preset_config_shadows_default` — a config preset named like a
  seed (`agentic-coding`) wins over the built-in.
- `test_find_preset_seed_when_config_empty` / `test_find_preset_unknown_returns_none`
  — seed fallback works; unknown names return None (regression: seed presets
  were once unresolvable everywhere but the GET endpoint).
- `test_load_malformed_yaml_returns_empty` — broken YAML degrades to an empty
  config instead of raising (the app must still boot).
- `test_delete_preset_referenced_raises` / `..._unreferenced_ok` — the 409
  protection layer: deleting a preset a server/alias references raises;
  otherwise it's removed from disk.

## test_runtime_command.py (4 tests) — launch command assembly

- `test_strip_flag` — `--flag value`, `--flag=value`, and repeated
  occurrences are all removed.
- `test_command_merge_precedence_last_wins` — with engine default
  `--temp 0.9`, preset `--temp 0.2`, server `--temp 0.6`, the final command
  contains all three IN THAT ORDER (llama.cpp last-flag-wins semantics — the
  server's value is the effective one).
- `test_ctx_size_computed_from_context_window_and_parallel` —
  `--ctx-size = context_window × parallel`.
- `test_host_from_config_and_extra_args_host_appended` — base command binds
  `Config.BIND_HOST`; a user-supplied `--host` in extra args lands after it
  (deliberate override, regression from the inverted "sanitizer").

## test_gateway_translation.py (9 tests) — Ollama↔OpenAI protocol layer

Pure functions behind agent tool-calling (silently dropped before the
gateway rework; these pin the translation contract).

- `_translate_options` ×3 — `num_predict`→`max_tokens` etc.; unknown Ollama
  options are dropped, never forwarded; None → `{}`.
- `_openai_tool_calls_to_ollama` ×3 — JSON-string arguments become dicts;
  unparseable argument strings are preserved as `{"_raw": ...}` instead of
  crashing the stream; a missing call id stays None.
- `_ollama_message_to_openai` ×2 — a `role: tool` message gains the
  `tool_call_id` field chat templates require; assistant tool_calls with
  dict arguments are re-serialized to JSON strings.
- `test_done_reason_mapping` — OpenAI `finish_reason` → Ollama `done_reason`
  (`tool_calls`/`length` pass through, everything else → `stop`).

## test_logs_rotation.py (2 tests) — crash forensics

Uses a real `subprocess.Popen(["cat"])` on a pipe as the fake server;
`LOGS_DIR` monkeypatched to `tmp_path`.

- `test_begin_capture_fills_ring` — captured stdout lands in the ring buffer
  and the log file.
- `test_second_begin_capture_rotates` — a second capture moves the previous
  file to `.log.1` (regression: crash logs used to be erased on restart).

## test_gguf.py (4 tests) — GGUF metadata parser

A synthetic GGUF header (magic, version 3, scalar KVs) is built with
`struct.pack` in `tmp_path`.

- `test_read_metadata_basic` — the parser returns the KVs written.
- `test_read_metadata_non_gguf_returns_none` — garbage file → None, no raise.
- `test_estimate_vram_gb_sane` — weights + KV-cache estimate is positive and
  ordered (more context ⇒ bigger estimate).
- `test_estimate_vram_gb_non_gguf_returns_none`.

## test_speed.py (5 tests) — timing-line parser

- `test_parse_decoded_line` / `test_parse_prompt_eval_line` — the two regexes
  extract generation and prompt tok/s from their line formats. NOTE: the
  expected line format is unverified against this machine's llama.cpp build —
  if the ⚡ chip never appears in the UI, these fixtures are the place to
  paste a real log line and adjust.
- `test_both_parsed_independently` — pp and gen update without clobbering
  each other.
- `test_garbage_no_crash` — arbitrary log lines never raise (the parser runs
  inside the log-capture thread; an exception there would kill log capture).
- `test_clear_speed` — a server's entry resets on restart.
