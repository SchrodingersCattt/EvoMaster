# Deferred Items - Phase 25

## Pre-existing Test Failures (Session Protocol Type Validation)

Plan 01 changed `PlaygroundContext.session` from `Any` to `Session | None`. Multiple test files use `MagicMock()` or `object()` as session values, which fail the `@runtime_checkable` Protocol isinstance check in Pydantic validation.

**Affected files (16 tests):**
- `tests/matmaster/devshell/test_runner.py` (4 tests) -- MagicMock from `_create_session` patch
- `tests/matmaster/devshell/test_integration.py` (1 test) -- same MagicMock issue
- `tests/matmaster/integration/test_compaction_real_api.py` (3 tests) -- MagicMock session
- `tests/matmaster/integration/test_subagent_spawn.py` (7 tests) -- MagicMock session in `_make_ctx()`
- `tests/matmaster/types/test_context.py` (2 tests) -- `object()` as session value

**Fix:** Use `MagicMock(spec=LocalSession)` or create a minimal stub class that implements Session Protocol methods. This is a cross-cutting fix that should be done in a dedicated cleanup pass, not scattered across plan-specific changes.

**Not caused by:** Plan 03 changes. This is from Plan 01's type annotation change.
