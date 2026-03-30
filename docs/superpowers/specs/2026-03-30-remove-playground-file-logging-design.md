# Remove Playground Per-Run File Logging

## Status: Approved

## Problem

`matmaster/core/playground.py` contains a per-run file logging infrastructure
(`_setup_logging`) that writes to `<run_dir>/logs/{task_id}.log`. No code in the
project reads these log files. All external information flows through the
database (chat persistence) and the frontend (SSE streaming). The log files
accumulate on disk with no consumer.

## Scope

Remove the per-run file logging infrastructure from `matmaster/core/playground.py`.
Module-level `logger` instances and their `logger.info/debug/...` statements are
**not** in scope -- they continue to output via the global application logging
configured in `src/utils/logger.py`.

## Changes

### matmaster/core/playground.py

1. **`__init__`**: Delete `self._log_file_handler` and `self._log_file_stream`
   attribute initializations (lines 61-63, the "Logging state" block).

2. **`prepare()`**: Delete the `# 5. Logging` comment and the
   `self._setup_logging(run_meta)` call (lines 109-110).

3. **`cleanup()`**: Delete the handler/stream release block (lines 132-147,
   from `# Release log handler` through `self._log_file_stream = None`).
   Update the docstring to remove the log-handler bullet.

4. **`_setup_logging()`**: Delete the entire method (lines 241-282).

### tests/matmaster/core/test_playground.py

- Delete `test_cleanup_releases_log_handler` (the test validates removed
  functionality).

## What stays

- `self.logger = logging.getLogger(self.__class__.__name__)` -- kept.
- `import logging` -- kept (used by `self.logger`).
- `cleanup()` session-close logic -- kept.
- Global application logging (`src/utils/logger.py`) -- unaffected.

## Risk

None. The removed code is write-only infrastructure with zero consumers.
