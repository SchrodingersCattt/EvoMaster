# Phase 34 Deferred Items

## Pre-existing Test Failures

All tests below follow the same pattern: they drain MessageBus expecting events from EventEmitterHook via the backward-compat kernel.run() path. After Plan 1 shifted events to the generator path, the bus receives nothing through the old run() path.

1. `tests/matmaster/devshell/test_integration.py::TestDevShellIntegration::test_full_run_with_tool_call`
2. `tests/matmaster/devshell/test_integration.py::TestDevShellIntegration::test_bus_events_emitted`
3. `tests/matmaster/integration/test_e2e_mat_master.py::TestMatMasterE2EPipeline::test_mat_master_e2e_pipeline`
4. `tests/matmaster/integration/test_e2e_mat_master.py::TestMatMasterE2EPipeline::test_mat_master_e2e_with_tool_call`
5. `tests/matmaster/integration/test_e2e_minimal.py::TestMinimalE2EPipeline::test_minimal_e2e_pipeline`
6. `tests/matmaster/integration/test_pipeline_alignment.py::TestEventSequenceAlignment::test_event_sequence_alignment`
7. `tests/matmaster/test_import_audit.py::TestPhase30FullIsolation::test_no_forbidden_imports_in_matmaster` (unrelated: new skill scripts with src imports)

- **Fix:** Update tests to use run_stream() path or kernel.run_stream() generator events instead of bus drain
- **Priority:** P2 (test quality improvement, not blocking production)
