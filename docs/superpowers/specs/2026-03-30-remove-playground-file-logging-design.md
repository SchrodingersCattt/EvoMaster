# Remove Playground Per-Run File Logging

## Status: Approved

## Problem

`matmaster/core/playground.py` contains a per-run file logging infrastructure
(`_setup_logging` / `_teardown_log_handler`) that writes to
`<run_dir>/logs/{task_id}.log`. No code in the project reads these log files.
All external information flows through the database (chat persistence) and the
frontend (SSE streaming). The log files accumulate on disk with no consumer.

## Scope

Remove the per-run file logging infrastructure from `matmaster/core/playground.py`.
Module-level `logger` instances and their `logger.info/debug/...` statements are
**not** in scope -- they continue to output via the global application logging
configured in `src/utils/logger.py`.

`playground/mat_master/evaluation/mat_runner.py` accesses `log_file_handler`
(no underscore prefix) and `_log_file_stream` via ``getattr`` with ``None``
defaults on `evomaster.core.playground` instances. After this change those
``getattr`` calls safely return ``None`` for any ``matmaster`` Playground
instance as well. The file is out of scope.

## Changes

### matmaster/core/playground.py

1. **Module docstring** (line 7): Remove the "Run-level file logging setup"
   bullet from the Responsibilities list.

2. **`__init__`** (lines 59-60): Delete `self._log_file_handler` and
   `self._log_file_stream` attribute initializations.

3. **`prepare()`** (line 101): Delete the `self._setup_logging(run_meta)` call.
   Update the docstring (line 67) to remove "logging" from
   "Create workspace, session, logging and return a frozen context."

4. **`cleanup()`** (line 126): Delete the `self._teardown_log_handler()` call.
   Update the docstring to remove the
   "Closes the log file handler/stream if present." bullet.

5. **`_setup_logging()`** (lines 209-239): Delete the entire method.

6. **`_teardown_log_handler()`** (lines 241-253): Delete the entire method.

7. **`PlaygroundManager` docstring** (line 344): Remove "logging" from
   "Does not touch Playground-internal environment prep (workspace, session, logging)."

### tests/matmaster/core/test_playground.py

- Delete `test_log_file_created` (validates removed `_setup_logging` behaviour).
- Delete `test_log_file_fallback_name` (validates removed `_setup_logging` behaviour).
- Delete `test_cleanup_releases_log_handler` (validates removed teardown behaviour).

## What stays

- `self.logger = logging.getLogger(self.__class__.__name__)` -- kept.
- `import logging` -- kept (used by `self.logger`).
- `cleanup()` session-close logic -- kept.
- Global application logging (`src/utils/logger.py`) -- unaffected.
- `evomaster/core/playground.py` logging -- separate class, out of scope.

## Risk

None. The removed code is write-only infrastructure with zero consumers.
