# Remove Playground Per-Run File Logging — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the unused per-run file logging infrastructure from `matmaster/core/playground.py`.

**Architecture:** Pure deletion — remove `_setup_logging`, `_teardown_log_handler`, their attribute state, call sites, and three tests that validate the removed behaviour. Update affected docstrings.

**Spec:** `docs/superpowers/specs/2026-03-30-remove-playground-file-logging-design.md`

---

## Chunk 1: Remove file logging infrastructure and update tests

### Task 1: Delete logging infrastructure from playground.py

**Files:**
- Modify: `matmaster/core/playground.py`

- [ ] **Step 1: Remove module docstring logging bullet**

In the module docstring (line 7), delete the line:
```
  - Run-level file logging setup
```

- [ ] **Step 2: Remove attribute initializations**

Delete lines 59-60 from `__init__`:
```python
        self._log_file_handler: logging.FileHandler | None = None
        self._log_file_stream = None
```

- [ ] **Step 3: Remove `_setup_logging` call from `prepare()`**

Delete line 101:
```python
        self._setup_logging(run_meta)
```

Update `prepare()` docstring:

- Line 67: `"Create workspace, session, logging and return a frozen context."` → `"Create workspace, session and return a frozen context."`
- Line 72: `"workspace/logging; str or Path"` → `"workspace; str or Path"`
- Line 74: `"workspace sub-path and log file name"` → `"workspace sub-path"`

- [ ] **Step 4: Remove `_teardown_log_handler()` call from `cleanup()`**

Delete line 126:
```python
        self._teardown_log_handler()
```

Update `cleanup()` docstring — delete the line:
```
        - Closes the log file handler/stream if present.
```

- [ ] **Step 5: Delete `_setup_logging()` method**

Delete the entire method at lines 209-239:
```python
    def _setup_logging(self, run_meta: dict[str, Any]) -> None:
        ...  # through line 239
```

- [ ] **Step 6: Delete `_teardown_log_handler()` method**

Delete the entire method at lines 241-253:
```python
    def _teardown_log_handler(self) -> None:
        ...  # through line 253
```

- [ ] **Step 7: Update `PlaygroundManager` docstring**

At line 344, change:
```python
    Does not touch Playground-internal environment prep (workspace, session, logging).
```
to:
```python
    Does not touch Playground-internal environment prep (workspace, session).
```

- [ ] **Step 8: Verify no syntax errors**

Run: `python -c "import matmaster.core.playground"`
Expected: no output (clean import)

### Task 2: Remove obsolete tests

**Files:**
- Modify: `tests/matmaster/core/test_playground.py`

- [ ] **Step 1: Delete `test_log_file_created`**

Delete lines 163-173 (the full test method including the trailing `pg.cleanup()`).

- [ ] **Step 2: Delete `test_log_file_fallback_name`**

Delete lines 175-185.

- [ ] **Step 3: Delete `test_cleanup_releases_log_handler`**

Delete lines 236-248 (the full test method inside `TestCleanup`).

- [ ] **Step 4: Run tests**

Run: `pytest tests/matmaster/core/test_playground.py -v`
Expected: all remaining tests PASS

### Task 3: Commit

- [ ] **Step 1: Commit all changes**

```bash
git add matmaster/core/playground.py tests/matmaster/core/test_playground.py
git commit -m "refactor(core): remove unused per-run file logging from Playground"
```
