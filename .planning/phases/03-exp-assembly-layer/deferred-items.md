# Deferred Items -- Phase 03

## Pre-existing Test Failures

1. **test_runtime.py::TestAgentRuntimeSpec::test_minimal_instantiation** -- Passes `object()` as `tool_registry` which is invalid since Plan 03-01 typed `tool_registry` as `ToolRegistry | None`. The test needs updating to use a real `ToolRegistry` instance or `None`. Found during Plan 03-03 Task 2 regression check.

2. **test_runtime.py::TestAgentRuntimeSpec::test_defaults** -- Same issue, passes `object()` as `tool_registry`.

3. **test_runtime.py::TestAgentRuntimeSpec::test_frozen** -- Same issue, passes `object()` as `tool_registry`.

All 9 tests in `TestAgentRuntimeSpec` class use `object()` for `tool_registry` and need updating to use `ToolRegistry()` or `None`. These are pre-existing failures from Plan 03-01's type constraint change and should be fixed in Phase 5 integration quality pass.
