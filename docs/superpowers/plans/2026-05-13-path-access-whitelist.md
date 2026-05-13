# Path Access Whitelist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow Read, Glob, and Grep to access runtime-approved roots such as the remote skill mirror and project `.matmaster` directories without weakening workspace path guards.

**Architecture:** Add explicit path access roots to `RuntimeTopology`, let `StructuralValidation` validate path arguments against workspace plus operation-scoped extra roots, and pass the same roots into shell-backed tools so their defensive path normalization matches Layer A. `SkillTool._render_skill_dir()` remains unchanged because it already renders the runtime-provided remote root.

**Tech Stack:** Python 3.11+, Pydantic models, existing Tool Runtime v2, pytest via `uv run pytest`.

---

### Task 1: Structural Path Access Roots

**Files:**
- Modify: `matmaster/types/topology.py`
- Modify: `matmaster/core/structural_validation.py`
- Test: `tests/matmaster/core/test_structural_validation.py`

- [ ] **Step 1: Write failing tests**

Add tests proving `Read` can access an absolute path under an extra read root, `Glob` can access an extra search root, `Write` is denied on a read/search-only extra root, and relative traversal into an extra root is still denied.

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/matmaster/core/test_structural_validation.py -q`

Expected: the new extra-root tests fail because `RuntimeTopology` has no access-root field and `StructuralValidation` only accepts `workspace_root`.

- [ ] **Step 3: Implement minimal model and validation support**

Add `PathAccessRoot` to `matmaster/types/topology.py`, add `RuntimeTopology.path_access_roots`, derive operation from tool capabilities, and validate absolute path arguments against roots that include the operation permission.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/matmaster/core/test_structural_validation.py -q`

Expected: all structural validation tests pass.

### Task 2: Tool Fallback Path Safety

**Files:**
- Modify: `matmaster/tools/builtin/_path_safety.py`
- Modify: `matmaster/tools/builtin/base.py`
- Modify: `matmaster/tools/builtin/glob_tool.py`
- Modify: `matmaster/tools/builtin/grep_tool.py`
- Test: `tests/matmaster/tools/builtin/test_path_safety.py`
- Test: `tests/matmaster/tools/builtin/test_glob_tool.py`
- Test: `tests/matmaster/tools/builtin/test_grep_tool.py`

- [ ] **Step 1: Write failing tests**

Add tests proving `resolve_safe_path()` preserves an absolute path under an allowed extra root and still falls back for non-whitelisted paths. Add Glob/Grep execution tests proving their generated command searches the extra root instead of falling back to the workspace.

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/matmaster/tools/builtin/test_path_safety.py tests/matmaster/tools/builtin/test_glob_tool.py tests/matmaster/tools/builtin/test_grep_tool.py -q`

Expected: new extra-root tests fail because tool fallback normalization only knows `workdir`.

- [ ] **Step 3: Implement tool-level extra roots**

Extend `BuiltinTool.__init__()` with an optional `path_access_roots` tuple, extend `resolve_safe_path()` with `allowed_roots`, and pass those roots from Glob/Grep calls.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/matmaster/tools/builtin/test_path_safety.py tests/matmaster/tools/builtin/test_glob_tool.py tests/matmaster/tools/builtin/test_grep_tool.py -q`

Expected: all path safety, Glob, and Grep tests pass.

### Task 3: Runtime Assembly Wiring

**Files:**
- Modify: `matmaster/core/exp.py`
- Test: `tests/matmaster/core/test_exp_runtime_v2.py`
- Test: `tests/matmaster/core/test_exp.py`

- [ ] **Step 1: Write failing tests**

Add tests proving `Exp.build_runtime()` registers `ctx.session.remote_project_root` and Bohrium `remote_workspace_root/.matmaster` as read/search roots, and passes those roots to Glob/Grep.

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/matmaster/core/test_exp_runtime_v2.py tests/matmaster/core/test_exp.py -q`

Expected: new tests fail because build runtime does not derive or pass extra path roots.

- [ ] **Step 3: Implement runtime root derivation**

Add an `Exp._derive_path_access_roots(ctx)` helper. Include `ctx.session.remote_project_root`, `ctx.run_meta["bohrium"]["remote_project_root"]`, and `ctx.run_meta["bohrium"]["remote_workspace_root"] + "/.matmaster"` when present. Deduplicate normalized roots and grant read/search only.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/matmaster/core/test_exp_runtime_v2.py tests/matmaster/core/test_exp.py -q`

Expected: all focused runtime assembly tests pass.

### Task 4: Final Verification

**Files:**
- No new files.

- [ ] **Step 1: Run all touched test modules**

Run: `uv run pytest tests/matmaster/core/test_structural_validation.py tests/matmaster/tools/builtin/test_path_safety.py tests/matmaster/tools/builtin/test_glob_tool.py tests/matmaster/tools/builtin/test_grep_tool.py tests/matmaster/core/test_exp_runtime_v2.py tests/matmaster/core/test_exp.py -q`

Expected: all selected tests pass.

- [ ] **Step 2: Inspect diff**

Run: `git diff -- matmaster/types/topology.py matmaster/core/structural_validation.py matmaster/core/exp.py matmaster/tools/builtin/_path_safety.py matmaster/tools/builtin/base.py matmaster/tools/builtin/glob_tool.py matmaster/tools/builtin/grep_tool.py tests/matmaster/core/test_structural_validation.py tests/matmaster/tools/builtin/test_path_safety.py tests/matmaster/tools/builtin/test_glob_tool.py tests/matmaster/tools/builtin/test_grep_tool.py tests/matmaster/core/test_exp_runtime_v2.py tests/matmaster/core/test_exp.py`

Expected: diff is scoped to runtime path access roots, shell-backed search fallback, and tests.
