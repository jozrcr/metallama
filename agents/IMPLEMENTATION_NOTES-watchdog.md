# Implementation Notes — Watchdog Batch (branch: feat/watchdog)

## Task 1: Log rotation

**What changed:** `metallama/app/logs.py`

- In `begin_capture()`: before opening the log file for writing, if the existing
  `logs/<server>.log` is non-empty, it is renamed to `logs/<server>.log.1`
  (replacing any prior `.1`). Wrapped in `try/except OSError: pass`.
- Added `previous_log_path(model_name) -> Path` module-level helper.

**Test output:**

```
=== Step 1: First start ===
  log lines: 4
    line-1783363581838659510
    log-entry-2
    done
    [metallama] process exited with code 0
  PASS (first_line='line-1783363581838659510')

=== Step 2: Second start ===
  .log.1 lines: 4
  .log lines: 4
  PASS (rotation confirmed)

=== Step 3: Third start ===
  files: ['rot.log', 'rot.log.1']
  PASS (only two files)

=== ALL TESTS PASSED ===
```

```
$ .venv/bin/python -m compileall -q metallama/app/
exit: 0
```

**Deviations:** None — implemented exactly as spec.

**Open questions:** None.
