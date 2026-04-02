# Phase 30 Deferred Items

## Pre-existing Test Failures (discovered in Plan 02)

These failures exist both with and without evomaster/, confirmed not caused by Plan 30-02 changes.

- `tests/matmaster/devshell/test_export_review_bundle.py` -- references missing evaluation/scripts/devshell/export_devshell_review_bundle.py
- `tests/matmaster/devshell/test_integration.py` -- PlaygroundContext Pydantic validation error (MagicMock not accepted as Session)
- `tests/matmaster/devshell/test_runner.py` -- Same PlaygroundContext Session typing issue
- `tests/matmaster/devshell/test_run_devshell_eval_script.py` -- references missing evaluation script files
- `tests/matmaster/integration/test_subagent_spawn.py` (7 tests) -- API mismatch in spawn lifecycle tests
- `tests/matmaster/integration/test_bohrium_execution_contract.py::test_skill_sync_spec` -- load_exp_config assertion
- `tests/matmaster/integration/test_e2e_mat_master.py` (2 tests) -- Bohrium SSE + abort event tests
- `tests/matmaster/integration/test_compaction_real_api.py` (3 tests) -- requires live LLM API
- `tests/matmaster/types/test_context.py` (2 tests) -- PlaygroundContext session field typing
