# Remove Skill Evolution from matmaster/ and src/

## Goal

Remove the deprecated skill_evolution feature from `matmaster/` and `src/` layers while preserving the skill sync infrastructure (uploading project skills to remote Bohrium containers).

## Scope

- `matmaster/integration/bohrium_setup.py` -- SkillSyncSpec and new derive function
- `src/services/agent_run_service.py` -- remove `_derive_skill_sync_spec`, update imports/call site
- `src/services/agent_run_bohrium.py` -- remove user skill upload branch
- `tests/matmaster/integration/test_bohrium_execution_contract.py` -- update fixtures and assertions
- `tests/matmaster/integration/test_upstream_scenarios.py` -- update SkillSyncSpec construction

Out of scope: `playground/` directory, `configs/mat_master/config.yaml`.

## Changes

### 1. SkillSyncSpec (bohrium_setup.py)

Remove `local_user_skills_root` and `remote_user_skills_root` fields. Only `project_skill_roots` and `remote_project_root` remain.

Update `remote_project_root` default from `/personal/workspace/.evomaster` to `/share/.matmaster`.

### 2. derive_skill_sync_spec (bohrium_setup.py)

Move `_derive_skill_sync_spec` from `agent_run_service.py` into `bohrium_setup.py` as public `derive_skill_sync_spec`. Changes:

- Remove `playground` parameter (only used for skill_evolution config extraction)
- Remove skill_evolution config extraction logic (lines 123-137 of original)
- Align variable naming to matmaster conventions (no single-letter vars, descriptive names)
- Pure function: ExpConfig + project_root -> SkillSyncSpec | None

### 3. agent_run_service.py

- Delete `_derive_skill_sync_spec` function definition (lines 90-144)
- Update import: add `derive_skill_sync_spec` from bohrium_setup, remove unused `SkillSyncSpec`
- Update call site: `derive_skill_sync_spec(exp_config, project_root=_project_root)`

### 4. agent_run_bohrium.py -- _sync_skills_to_ssh_session

- Remove user skill upload branch (lines 166-175)
- Remove `ssh_session.remote_user_skills_root` and `ssh_session.local_user_skills_root` assignments
- Keep `ssh_session.remote_project_root` assignment

### 5. Tests

- Remove `skill_evolution` mock config from playground mock
- Remove `local_user_skills_root` / `remote_user_skills_root` from all SkillSyncSpec constructions
- Update `remote_project_root` assertions to `/share/.matmaster`
