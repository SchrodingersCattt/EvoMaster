# PlotFigure Tool Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the figure-collection chain out of `BashTool` into a single model-visible builtin tool `PlotFigure` that both generates-and-publishes and publishes-existing figures, replacing the brittle `$ARTIFACT_DIR` + `$MANIFEST_PATH` manifest contract with explicit `output_path` + `caption` tool arguments.

**Architecture:** Extract a pure bash execution core (`run_bash_command`) shared by `BashTool` and `PlotFigure`. Add a declared-figure collection pipeline (`collect_declared_figure`) to `figure_artifacts.py` that resolves a workspace path, downloads, validates, auto-generates a stable `figure_id`, uploads, and emits the existing `FigureDescriptor`. `PlotFigure` runs an optional command then collects one declared figure into `ToolResult.payload["figures"]`. The downstream response-figures aggregation (`FigureCoordinator` -> `ResponseFiguresAccumulator` -> `ResponseFiguresEvent`) is untouched; it already ingests `payload["figures"]` regardless of tool identity or result status. The old manifest chain is deleted with no compatibility shims.

**Tech Stack:** Python 3.13 via `uv run`, Pydantic models (`ToolResult`, `FigureDescriptor`, `FigureUploadConfig`), `BuiltinTool` ABC, session shell abstraction, pytest with `unittest.mock`.

---

## Source Spec

- `docs/superpowers/specs/2026-06-01-plot-figure-tool-design.md`

## Required Sub-Skills

- @superpowers:test-driven-development — every task is RED -> GREEN -> COMMIT.
- @superpowers:verification-before-completion — run the stated command and confirm output before checking a step.

## Current Code Facts (verified against this checkout)

- `BashTool` owns the figure chain today: figure imports at `matmaster/tools/builtin/bash_tool.py:19-25`, manifest prompt text at `bash_tool.py:114-118`, figure execution at `bash_tool.py:130-264`.
- The pure execution body to extract lives at `bash_tool.py:182-226` (`plan_shell_command`, `prepare_inline_command`/`prepare_script_command`, `get_runtime().build_env()`, `session.exec_bash`, observation assembly).
- `figure_artifacts.py` helpers to REUSE: `_download_with_retry:264`, `_validate_image_bytes:275`, `_sniff_image_format:290`, `_build_asset_key:300`, `_sanitize_key_segment:322`, `_upload_with_retry:327`.
- `figure_artifacts.py` symbols to DELETE in Chunk 4: `build_figure_env:57`, `collect_figures_from_session:121`, `_load_manifest:187`, `_ManifestLoadResult:51`, `_resolve_artifact_path:248`, `FigureCollectionResult:44`.
- `_link_figure_into_flat_view:67` currently derives `flat_dir` from `artifact_dir` (`bash_tool` path only). New code needs it `workdir`-based.
- `BuiltinTool.validate_input` is `async` and returns `ToolDecision | None` (`base.py:92-98`); deny pattern is `ToolDecision(decision="deny", reason=..., guidance=...)` (`write_tool.py:80-95`).
- `ToolExecutionContext` fields are `cancel_token`, `on_progress`, `runner_state`, `tool_call_id` (`tool_spec.py:89-101`).
- `runner_state["figure_upload_config"]` is injected at `exp.py:392-394`; `tool_call_id` arrives via `exec_ctx` per call.
- Session methods used: `path_exists`, `is_file` (`grep_tool.py:299`), `download`, `exec_bash`, `write_file`.
- `resolve_safe_path` silently falls back to `workdir` on out-of-bounds input (`_path_safety.py:53,59`) — that is why `PlotFigure` needs its own deny-on-escape resolver, not `resolve_safe_path`.
- Downstream load-bearing invariant CONFIRMED: the kernel's `ToolResultEvent` construction in `agent_tool_dispatch.py` sets `status` and `payload` as independent kwargs, and `ResponseFiguresAccumulator.add_tool_result` in `response_figures_service.py` reads `event.payload['figures']` without gating on status. So an `error`-status result with figures still publishes. Do not change either file. NOTE: `agent_tool_dispatch.py` is under active change from a parallel token-usage effort — do not cite line numbers for it; re-read before relying on any offset.
- `FigureDescriptor` fields: `figure_id`, `asset_url`, `caption`, `alt`, `importance`, `placement_hint`, `source_tool_call_id`, `remote_path` (`figures.py:24-35`). The new tool only sets `figure_id`/`asset_url`/`caption`/`source_tool_call_id`/`remote_path`; the rest keep model defaults.
- Tool registration lives at `exp.py:702-719` (`session_tools`), `exp.py:93-107` (`_SESSION_REQUIRING_TOOL_NAMES`), `exp.py:664-668` (docstring tool list).
- Existing prompt guidance on `[[fig:<figure_id>]]` is `matmaster/exps/_base.toml:46-47`. The manifest mechanics live in `BashTool.prompt()`, not the toml.
- Test conventions (`tests/matmaster/tools/builtin/test_bash_tool.py:21-51`): `make_session(...)` returns a `MagicMock` whose `exec_bash` returns a dict; `attach_test_runtime(session)` wires a `BohriumRuntimeHandle`. That file currently imports `FigureCollectionResult, build_figure_env` (`:10`) and `FigureUploadConfig` (`:14`) — both must be cleaned up in Chunk 4.

## File Map

Create:
- `matmaster/tools/bash_runner.py` — `BashRunResult` dataclass + `run_bash_command(...)`. Pure execution core only: no figure, upload, or path-validation concerns.
- `matmaster/tools/builtin/plot_figure_tool.py` — `PlotFigure(BuiltinTool)`.
- `tests/matmaster/tools/test_bash_runner.py`
- `tests/matmaster/tools/test_collect_declared_figure.py`
- `tests/matmaster/tools/builtin/test_plot_figure_tool.py`
- `tests/matmaster/services/test_plot_figure_aggregation.py`

Modify:
- `matmaster/tools/figure_artifacts.py` — add `resolve_workspace_output_path`, `build_figure_id`, `FigureValidationError`, `DeclaredFigureResult`, `collect_declared_figure`, `_link_figure_flat`; in Chunk 4 delete the manifest pipeline.
- `matmaster/tools/builtin/bash_tool.py` — Chunk 1 delegate execution to `run_bash_command` (behavior unchanged); Chunk 4 strip all figure logic.
- `matmaster/tools/builtin/__init__.py` — export `PlotFigure`.
- `matmaster/core/exp.py` — register `PlotFigure`, extend `_SESSION_REQUIRING_TOOL_NAMES`, update docstring.
- `matmaster/exps/direct.toml`, `matmaster/exps/planner.toml` — add `PlotFigure` to explicit builtin allowlists so the global `_base.toml` figure guidance names an actually available tool in the main user-facing modes.
- `matmaster/types/figures.py` — Chunk 4 delete `FigureManifestEntry`.
- `matmaster/types/__init__.py` — Chunk 4 drop `FigureManifestEntry` export.
- `matmaster/exps/_base.toml` — Chunk 4 prompt migration.
- `tests/matmaster/core/test_exp.py` — update builtin registration counts/sets and assert direct/planner config exposure.
- `tests/matmaster/tools/builtin/test_bash_tool.py`, `tests/matmaster/tools/test_figure_artifacts.py`, `tests/matmaster/tools/test_figure_artifacts_real_fs.py`, `tests/matmaster/types/test_figures.py` — Chunk 4 migrate/delete old manifest tests.

## Constraints

- Verification uses `uv run pytest`, never system Python. This project has NO ruff or linter configured (dev-deps are pytest + pytest-asyncio only); use `uv run pytest --collect-only` as the import/syntax gate (matching existing plans), and verify dead-import removal by inspection plus a green suite — never a linter command.
- No compatibility shims for the old manifest chain. Migration is delete-and-replace (project rule: prefer migration over compatibility).
- Do NOT touch `src/services/figure_coordinator.py`, `src/services/response_figures_service.py`, `matmaster/core/agent_tool_dispatch.py`, `matmaster/types/events.py:ResponseFiguresEvent`, `FigureDescriptor`, `FigureUploadConfig`, or `agent_run_bohrium_stage.py:_build_figure_upload_config`.
- `PlotFigure` publishes exactly one figure per call. Multi-figure = multiple calls (may be batched in one turn; `tool_runner.execute_batch` accepts a sequence).
- `output_path` containment is purely lexical (`posixpath.normpath` + `is_relative_to`); it does not resolve symlinks. This matches the existing `WriteTool`/`resolve_safe_path` boundary model. Symlink-escape inside the user's own workspace is accepted as out of scope.

### Deltas from the spec (deliberate refinements)

- **Error classification mechanism (spec §9.2 left this unspecified).** `_validate_image_bytes` currently bundles three failures into one `ValueError`. This plan refactors it to raise `FigureValidationError(reason=...)` which **subclasses `ValueError`** so the existing `collect_figures_from_session` (`except Exception`) and any `pytest.raises(ValueError)` stay green until Chunk 4 deletes them. `collect_declared_figure` reads `exc.reason` — stable classification with no exception-text parsing. `download_failed`/`upload_failed` are classified by call site, not text.
- **`_link_figure_into_flat_view` signature.** Instead of swapping `artifact_dir` for `workdir` (which would break the still-live old caller mid-plan), Chunk 2 extracts the shell body into `_link_figure_flat(*, session, flat_dir, ...)`. Both the old manifest path and the new declared path compute their own `flat_dir` and call it. Chunk 4 deletes the old wrapper and keeps `_link_figure_flat`. Net result equals the spec's workdir-based flat view.
- **Explicit exp allowlists.** The spec records explicit builtin allowlist migration as a configuration decision, but this plan intentionally includes `direct.toml` and `planner.toml`: `_base.toml` is global, and those two modes are the primary user-facing modes that need figure publication. Without this migration, the prompt would tell the model to call `PlotFigure` while the configured tool list withholds it.

### Known tradeoffs (recorded, not action items)

- `importance`/`placement_hint`/`alt` are no longer settable; all `PlotFigure` figures take defaults `secondary`/`sidebar_only`/`None`. No in-repo consumer reads these fields (verified), but `FigureDescriptor` is emitted to clients, so a frontend that distinguished primary figures or used alt text loses that signal.
- `PlotFigure(command=...)` is a second arbitrary-shell entry point sharing `run_bash_command`. There is currently no command-content safety policy in `matmaster`/`src`. If one is ever added it MUST hook the shared `run_bash_command` core, not `BashTool` alone, or the `PlotFigure` path bypasses it.

---

## Chunk 1: Shared bash execution core

Extract pure command execution so `BashTool` and `PlotFigure` share one code path. `BashTool` keeps its figure logic in this chunk (delegating only the exec step), so all existing tests stay green.

### Task 1: Create `run_bash_command` execution core

**Files:**
- Create: `matmaster/tools/bash_runner.py`
- Test: `tests/matmaster/tools/test_bash_runner.py`

- [ ] **Step 1: Write the failing test**

Create `tests/matmaster/tools/test_bash_runner.py`:

```python
"""tests/matmaster/tools/test_bash_runner.py"""

from unittest.mock import MagicMock

from matmaster.tools.bash_runner import BashRunResult, run_bash_command


def make_session(output="ok", exit_code=0, working_dir="/share"):
    s = MagicMock()
    s.exec_bash.return_value = {
        "output": output,
        "exit_code": exit_code,
        "working_dir": working_dir,
    }
    return s


def test_run_bash_command_assembles_observation():
    session = make_session(output="hello", exit_code=0, working_dir="/share")
    result = run_bash_command(
        session=session,
        command="echo hello",
        timeout_s=2.0,
        cancel_token=None,
    )
    assert isinstance(result, BashRunResult)
    assert result.output == "hello"
    assert result.exit_code == 0
    assert result.working_dir == "/share"
    assert "[Session working directory: /share]" in result.observation
    assert "[Command finished with exit code 0]" in result.observation


def test_run_bash_command_passes_cancel_token_and_timeout():
    session = make_session()
    sentinel = object()
    run_bash_command(
        session=session,
        command="true",
        timeout_s=5.0,
        cancel_token=sentinel,
    )
    _, kwargs = session.exec_bash.call_args
    assert kwargs["timeout"] == 5.0
    assert kwargs["cancel_token"] is sentinel


def test_run_bash_command_merges_extra_env_without_runtime():
    session = make_session()
    # No runtime attached -> base env empty; extra_env still applied via script_env.
    run_bash_command(
        session=session,
        command="echo $ARTIFACT_DIR",
        timeout_s=2.0,
        cancel_token=None,
        extra_env={"ARTIFACT_DIR": "/share/.artifacts"},
    )
    # Non-empty env is injected via a temp env file (script_env._via_file),
    # so write_file must have been called and the command actually ran. This
    # guards the merge: a dropped extra_env would skip env injection entirely.
    assert session.write_file.called
    assert session.exec_bash.called
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/matmaster/tools/test_bash_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'matmaster.tools.bash_runner'`.

- [ ] **Step 3: Write the minimal implementation**

Create `matmaster/tools/bash_runner.py`:

```python
"""matmaster/tools/bash_runner.py

Shared bash execution core extracted from BashTool. Pure command
execution only: plan, env injection, exec, observation assembly.
No figure, upload, or path-validation concerns. Timeout-cap policy
stays in each calling tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from matmaster.bohrium.runtime import get_runtime
from matmaster.tools.filesystem_semantics.shell_planner import plan_shell_command
from matmaster.tools.script_env import (
    prepare_inline_command,
    prepare_script_command,
)
from matmaster.types.cancellation import CancellationToken


@dataclass(slots=True)
class BashRunResult:
    output: str
    exit_code: int
    working_dir: str
    observation: str


def run_bash_command(
    *,
    session: Any,
    command: str,
    timeout_s: float,
    cancel_token: CancellationToken | None,
    extra_env: dict[str, str] | None = None,
) -> BashRunResult:
    runtime = get_runtime(session)
    env = runtime.build_env() if runtime is not None else {}
    if extra_env:
        env = {**env, **extra_env}

    plan = plan_shell_command(command)
    if plan.mode == "script":
        prepared = prepare_script_command(command, env, session, shell_path="bash")
    else:
        prepared = prepare_inline_command(command, env, session)

    result = session.exec_bash(
        command=prepared,
        timeout=timeout_s,
        cancel_token=cancel_token,
    )

    output = result.get("output", "") or result.get("stdout", "")
    exit_code = result.get("exit_code", 0)
    working_dir = result.get("working_dir", "")

    observation = output
    if working_dir:
        observation += f"\n[Session working directory: {working_dir}]"
    observation += f"\n[Command finished with exit code {exit_code}]"

    return BashRunResult(
        output=output,
        exit_code=exit_code,
        working_dir=working_dir,
        observation=observation,
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/matmaster/tools/test_bash_runner.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/bash_runner.py tests/matmaster/tools/test_bash_runner.py
git commit -m "feat: add shared run_bash_command execution core"
```

### Task 2: Delegate `BashTool` execution to `run_bash_command`

Keep figure logic intact; only the exec/observation step moves to the shared core. Behavior is unchanged, so every existing `BashTool` test must still pass.

**Files:**
- Modify: `matmaster/tools/builtin/bash_tool.py:161-268`
- Test: `tests/matmaster/tools/builtin/test_bash_tool.py` (existing suite, no new tests)

- [ ] **Step 1: Replace the execution body, keep figure env + collection**

In `bash_tool.py`, replace the body of `_execute_with_figure_support` (lines 161-268) with the version below. Keep all imports for now. Add `from matmaster.tools.bash_runner import run_bash_command` to the import block.

```python
    def _execute_with_figure_support(
        self,
        arguments: dict[str, Any],
        figure_cfg: FigureUploadConfig | None = None,
        tool_call_id: str | None = None,
    ) -> str | ToolResult:
        session = self._require_session()

        command: str = (arguments.get("command") or "").strip()
        if not command:
            return "Error: command is required and must not be empty."

        timeout_ms = int(arguments.get("timeout", 120_000))
        cap = (
            _SLEEP_TIMEOUT_CAP_MS
            if _PURE_SLEEP_RE.fullmatch(command)
            else _GENERAL_TIMEOUT_CAP_MS
        )
        timeout_s = min(timeout_ms, cap) / 1000

        extra_env: dict[str, str] | None = None
        artifact_dir: str | None = None
        manifest_path: str | None = None
        if figure_cfg is not None and tool_call_id and self._workdir is not None:
            artifact_dir, manifest_path = build_figure_env(
                str(self._workdir),
                tool_call_id,
            )
            session.exec_bash(f"mkdir -p {shlex.quote(artifact_dir)}")
            extra_env = {
                "ARTIFACT_DIR": artifact_dir,
                "MANIFEST_PATH": manifest_path,
            }

        run = run_bash_command(
            session=session,
            command=command,
            timeout_s=timeout_s,
            cancel_token=self._cancel_token_for_exec(),
            extra_env=extra_env,
        )
        obs = run.observation
        exit_code = run.exit_code

        if (
            figure_cfg is not None
            and tool_call_id is not None
            and artifact_dir is not None
            and manifest_path is not None
        ):
            collection = collect_figures_from_session(
                session=session,
                artifact_dir=artifact_dir,
                manifest_path=manifest_path,
                tool_call_id=tool_call_id,
                upload_config=figure_cfg,
            )
            if collection.figures or collection.failure_ids or collection.warnings:
                content = obs
                if collection.failure_ids:
                    content += (
                        "\n[Figure pipeline: "
                        f"{len(collection.failure_ids)} failed: "
                        + ", ".join(collection.failure_ids)
                        + "]"
                    )
                if collection.warnings:
                    content += (
                        "\n[Figure manifest ignored: "
                        + "; ".join(collection.warnings)
                        + "]"
                    )
                return ToolResult(
                    status="error" if exit_code != 0 else "success",
                    content=content,
                    payload={
                        "figures": [
                            fig.model_dump(mode="json") for fig in collection.figures
                        ]
                    },
                )

        if exit_code != 0:
            return ToolResult(status="error", content=obs)
        return obs
```

Then delete the imports that moved into `run_bash_command`: the top-level `from matmaster.bohrium.runtime import get_runtime` and `from matmaster.tools.filesystem_semantics.shell_planner import plan_shell_command`, plus the method-local `from matmaster.tools.script_env import (prepare_inline_command, prepare_script_command)`. KEEP `shlex` (figure `mkdir -p`), `build_figure_env`, `collect_figures_from_session`, `FigureUploadConfig`, and `ValidationError` — the figure path still needs them in this chunk.

- [ ] **Step 2: Run the full BashTool suite to verify no behavior change**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bash_tool.py -v`
Expected: all existing tests pass (figure cases still green because the figure pipeline is unchanged).

- [ ] **Step 3: Smoke-import both modules**

Run: `uv run python -c "import matmaster.tools.builtin.bash_tool, matmaster.tools.bash_runner"`
Expected: no ImportError (confirms no still-needed import was deleted). No ruff exists in this project; dead-import removal is confirmed by inspection plus the green suite in Step 2.

- [ ] **Step 4: Commit**

```bash
git add matmaster/tools/builtin/bash_tool.py
git commit -m "refactor: route BashTool execution through run_bash_command"
```

---

## Chunk 2: Declared-figure collection pipeline

Add the path resolver, stable id generator, typed validation error, workdir-based flat symlink, and `collect_declared_figure` to `figure_artifacts.py`. The old manifest pipeline still works alongside these additions.

### Task 3: Add `resolve_workspace_output_path`

**Files:**
- Modify: `matmaster/tools/figure_artifacts.py`
- Test: `tests/matmaster/tools/test_collect_declared_figure.py`

- [ ] **Step 1: Write the failing test**

Create `tests/matmaster/tools/test_collect_declared_figure.py`:

```python
"""tests/matmaster/tools/test_collect_declared_figure.py"""

from matmaster.tools.figure_artifacts import resolve_workspace_output_path


def test_relative_path_joins_workspace():
    assert (
        resolve_workspace_output_path(raw_path="band.png", workdir="/share")
        == "/share/band.png"
    )


def test_nested_relative_path():
    assert (
        resolve_workspace_output_path(raw_path="results/xrd.png", workdir="/share")
        == "/share/results/xrd.png"
    )


def test_absolute_inside_workspace_ok():
    assert (
        resolve_workspace_output_path(raw_path="/share/a/b.png", workdir="/share")
        == "/share/a/b.png"
    )


def test_escape_relative_denied():
    assert (
        resolve_workspace_output_path(raw_path="../escape.png", workdir="/share")
        is None
    )


def test_escape_absolute_denied():
    assert (
        resolve_workspace_output_path(raw_path="/etc/passwd", workdir="/share")
        is None
    )
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/matmaster/tools/test_collect_declared_figure.py -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_workspace_output_path'`.

- [ ] **Step 3: Implement the resolver**

In `matmaster/tools/figure_artifacts.py`, add `from pathlib import PurePosixPath` to the imports, then add:

```python
def resolve_workspace_output_path(
    *,
    raw_path: str,
    workdir: str | PurePosixPath,
) -> str | None:
    """Resolve a declared output path against the workspace root.

    Returns the normalized absolute path if it stays inside ``workdir``,
    or None if it escapes. Containment is lexical (no symlink resolution),
    matching WriteTool's boundary model. Unlike resolve_safe_path, an
    escape returns None (deny) rather than silently falling back to workdir.
    """
    root = PurePosixPath(posixpath.normpath(str(workdir)))
    candidate = (
        raw_path
        if posixpath.isabs(raw_path)
        else posixpath.join(str(root), raw_path)
    )
    resolved = PurePosixPath(posixpath.normpath(candidate))
    if not resolved.is_relative_to(root):
        return None
    return str(resolved)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/matmaster/tools/test_collect_declared_figure.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/figure_artifacts.py tests/matmaster/tools/test_collect_declared_figure.py
git commit -m "feat: add resolve_workspace_output_path for declared figures"
```

### Task 4: Add `build_figure_id` stable id generator

**Files:**
- Modify: `matmaster/tools/figure_artifacts.py`
- Test: `tests/matmaster/tools/test_collect_declared_figure.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/matmaster/tools/test_collect_declared_figure.py`:

```python
from matmaster.tools.figure_artifacts import build_figure_id

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_figure_id_sanitizes_spaces():
    fid = build_figure_id(output_path="plots/band structure.png", image_bytes=_PNG)
    stem, _, digest = fid.rpartition("-")
    assert stem == "band-structure"
    assert len(digest) == 12


def test_figure_id_non_ascii_stem_falls_back_to_figure():
    fid = build_figure_id(output_path="结果图.png", image_bytes=_PNG)
    assert fid.startswith("figure-")


def test_figure_id_is_deterministic_for_same_bytes():
    a = build_figure_id(output_path="x.png", image_bytes=_PNG)
    b = build_figure_id(output_path="x.png", image_bytes=_PNG)
    assert a == b


def test_figure_id_changes_with_bytes():
    a = build_figure_id(output_path="x.png", image_bytes=_PNG)
    b = build_figure_id(output_path="x.png", image_bytes=_PNG + b"x")
    assert a != b


def test_figure_id_length_bounded_and_charset():
    fid = build_figure_id(output_path="A" * 200 + ".png", image_bytes=_PNG)
    assert len(fid) <= 64
    assert all(c.isalnum() or c in "._-" for c in fid)
    assert "/" not in fid
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/matmaster/tools/test_collect_declared_figure.py -k figure_id -v`
Expected: FAIL with `ImportError: cannot import name 'build_figure_id'`.

- [ ] **Step 3: Implement the generator**

In `figure_artifacts.py`, add near the other constants:

```python
_FIGURE_ID_STEM_MAX = 48
_FIGURE_ID_TOTAL_MAX = 64
```

and the function:

```python
def build_figure_id(*, output_path: str, image_bytes: bytes) -> str:
    """Stable, sanitized figure_id: sanitized stem + sha256(bytes)[:12].

    Charset limited to [A-Za-z0-9._-]; other runs fold to '-'; consecutive
    '-' merge; leading/trailing '-' stripped; empty stem -> 'figure';
    stem capped at 48 chars, total capped at 64. Never contains '/', NUL,
    control chars, or whitespace.
    """
    stem = posixpath.splitext(posixpath.basename(output_path))[0]
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", stem)
    sanitized = re.sub(r"-{2,}", "-", sanitized).strip("-")
    sanitized = sanitized[:_FIGURE_ID_STEM_MAX].strip("-")
    if not sanitized:
        sanitized = "figure"
    digest = hashlib.sha256(image_bytes).hexdigest()[:12]
    return f"{sanitized}-{digest}"[:_FIGURE_ID_TOTAL_MAX]
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/matmaster/tools/test_collect_declared_figure.py -k figure_id -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/figure_artifacts.py tests/matmaster/tools/test_collect_declared_figure.py
git commit -m "feat: add build_figure_id stable id generator"
```

### Task 5: Refactor `_validate_image_bytes` to `FigureValidationError`

**Files:**
- Modify: `matmaster/tools/figure_artifacts.py:275-287`
- Test: `tests/matmaster/tools/test_collect_declared_figure.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/matmaster/tools/test_collect_declared_figure.py`:

```python
import pytest

from matmaster.tools.figure_artifacts import (
    FigureValidationError,
    _validate_image_bytes,
)

_JPG = b"\xff\xd8\xff" + b"\x00" * 64


def test_validate_unsupported_format_reason():
    with pytest.raises(FigureValidationError) as exc:
        _validate_image_bytes(payload=_PNG, path="/share/x.gif")
    assert exc.value.reason == "unsupported_format"


def test_validate_header_mismatch_reason():
    # .png suffix but JPG magic bytes
    with pytest.raises(FigureValidationError) as exc:
        _validate_image_bytes(payload=_JPG, path="/share/x.png")
    assert exc.value.reason == "image_header_mismatch"


def test_validate_too_large_reason():
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (10 * 1024 * 1024 + 1)
    with pytest.raises(FigureValidationError) as exc:
        _validate_image_bytes(payload=big, path="/share/x.png")
    assert exc.value.reason == "figure_too_large"


def test_validation_error_is_value_error_subclass():
    # Keeps the old manifest pipeline's `except Exception` / ValueError contract.
    assert issubclass(FigureValidationError, ValueError)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/matmaster/tools/test_collect_declared_figure.py -k validat -v`
Expected: FAIL with `ImportError: cannot import name 'FigureValidationError'`.

- [ ] **Step 3: Add the exception and update `_validate_image_bytes`**

In `figure_artifacts.py`, add after the imports:

```python
class FigureValidationError(ValueError):
    """Image validation failure carrying a stable classification reason.

    Subclasses ValueError so existing callers that catch ValueError keep
    working; new callers read ``.reason`` for stable classification.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}:{detail}" if detail else reason)
```

Replace the body of `_validate_image_bytes` (lines 275-287) with:

```python
def _validate_image_bytes(*, payload: bytes, path: str) -> None:
    suffix = posixpath.splitext(path)[1].lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise FigureValidationError("unsupported_format", suffix)
    if len(payload) > _MAX_FIGURE_BYTES:
        raise FigureValidationError("figure_too_large", str(len(payload)))
    sniffed = _sniff_image_format(payload)
    if sniffed is None:
        raise FigureValidationError("image_header_mismatch", suffix)
    if suffix in {".jpg", ".jpeg"} and sniffed == ".jpg":
        return
    if sniffed != suffix:
        raise FigureValidationError("image_header_mismatch", suffix)
```

- [ ] **Step 4: Run the new tests AND the old manifest suite to verify both green**

Run: `uv run pytest tests/matmaster/tools/test_collect_declared_figure.py -k validat tests/matmaster/tools/test_figure_artifacts.py -v`
Expected: new tests pass; old `test_figure_artifacts.py` still passes (ValueError subclass preserves the contract).

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/figure_artifacts.py tests/matmaster/tools/test_collect_declared_figure.py
git commit -m "refactor: raise typed FigureValidationError from image validation"
```

### Task 6: Add workdir-based `_link_figure_flat`

Extract the symlink shell body so both the old (`artifact_dir`-derived) and new (`workdir`-derived) callers share it via an explicit `flat_dir`.

**Files:**
- Modify: `matmaster/tools/figure_artifacts.py:67-118`
- Test: `tests/matmaster/tools/test_collect_declared_figure.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/matmaster/tools/test_collect_declared_figure.py`:

```python
from unittest.mock import MagicMock

from matmaster.tools.figure_artifacts import _link_figure_flat


def test_link_figure_flat_builds_relative_symlink():
    session = MagicMock()
    session.exec_bash.return_value = {"exit_code": 0, "stdout": ""}
    _link_figure_flat(
        session=session,
        flat_dir="/share/.matmaster/figures",
        resolved_path="/share/results/band.png",
        figure_id="band-abc123",
    )
    cmd = session.exec_bash.call_args.kwargs.get("command") or session.exec_bash.call_args.args[0]
    assert "/share/.matmaster/figures/band-abc123.png" in cmd
    # rel target from flat_dir to resolved_path
    assert "../../results/band.png" in cmd
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/matmaster/tools/test_collect_declared_figure.py -k link_figure_flat -v`
Expected: FAIL with `ImportError: cannot import name '_link_figure_flat'`.

- [ ] **Step 3: Extract `_link_figure_flat`, make the old wrapper delegate**

In `figure_artifacts.py`, add `_link_figure_flat` taking an explicit `flat_dir` (move the shell body from `_link_figure_into_flat_view` lines 82-118 verbatim, swapping the computed `flat_dir` for the parameter):

```python
def _link_figure_flat(
    *,
    session: Session,
    flat_dir: str,
    resolved_path: str,
    figure_id: str,
) -> None:
    """Create a flat-view symlink <flat_dir>/<figure_id><suffix> -> resolved_path.

    Diagnostics are logged only; symlink failures never affect figure
    collection. Uses an explicit [ -e ]/[ -L ] guard to reject any existing
    link_path, including dangling symlinks.
    """
    suffix = posixpath.splitext(resolved_path)[1].lower()
    link_path = posixpath.join(flat_dir, f"{figure_id}{suffix}")
    rel_target = posixpath.relpath(resolved_path, start=flat_dir)
    safe_figure_id = _format_figure_id_for_diagnostic(figure_id)

    q_flat = shlex.quote(flat_dir)
    q_link = shlex.quote(link_path)
    q_target = shlex.quote(rel_target)
    q_marker = shlex.quote(_SYMLINK_EXISTS_MARKER)

    cmd = (
        f"mkdir -p -- {q_flat} && "
        f"if [ -e {q_link} ] || [ -L {q_link} ]; then "
        f"printf '%s\\n' {q_marker} && "
        f"exit {_SYMLINK_EXISTS_EXIT_CODE}; "
        f"fi && "
        f"ln -s -- {q_target} {q_link}"
    )

    try:
        exec_result = session.exec_bash(command=cmd)
    except Exception as exc:
        logger.warning("figure_symlink_failed:%s:%s", safe_figure_id, exc)
        return

    exit_code = exec_result.get("exit_code", 0)
    if exit_code == 0:
        return

    stdout = exec_result.get("stdout", "")
    if exit_code == _SYMLINK_EXISTS_EXIT_CODE or _SYMLINK_EXISTS_MARKER in stdout:
        logger.warning("figure_symlink_exists:%s", safe_figure_id)
        return

    err = exec_result.get("stderr", "") or stdout
    snippet = err[:200].strip()
    logger.warning("figure_symlink_failed:%s:%s", safe_figure_id, snippet)
```

Then shrink the existing `_link_figure_into_flat_view` (still used by the old manifest path) to a thin wrapper:

```python
def _link_figure_into_flat_view(
    *,
    session: Session,
    artifact_dir: str,
    resolved_path: str,
    figure_id: str,
) -> None:
    flat_dir = posixpath.dirname(posixpath.dirname(posixpath.normpath(artifact_dir)))
    _link_figure_flat(
        session=session,
        flat_dir=flat_dir,
        resolved_path=resolved_path,
        figure_id=figure_id,
    )
```

- [ ] **Step 4: Run new + old suites to verify both green**

Run: `uv run pytest tests/matmaster/tools/test_collect_declared_figure.py -k link_figure_flat tests/matmaster/tools/test_figure_artifacts.py tests/matmaster/tools/test_figure_artifacts_real_fs.py -v`
Expected: new test passes; old flat-view tests still pass.

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/figure_artifacts.py tests/matmaster/tools/test_collect_declared_figure.py
git commit -m "refactor: extract flat_dir-based _link_figure_flat"
```

### Task 7: Add `DeclaredFigureResult` + `collect_declared_figure`

**Files:**
- Modify: `matmaster/tools/figure_artifacts.py`
- Test: `tests/matmaster/tools/test_collect_declared_figure.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/matmaster/tools/test_collect_declared_figure.py`:

```python
from matmaster.tools.figure_artifacts import (
    DeclaredFigureResult,
    collect_declared_figure,
)
from matmaster.types.figures import FigureUploadConfig


def make_upload_config(url="https://assets.test/u/fig.png"):
    return FigureUploadConfig(
        session_id="sess-1",
        task_id="task-1",
        asset_key_prefix="figs",
        upload_bytes=lambda payload, key: url,
    )


def make_fig_session(*, exists=True, is_file=True, payload=_PNG):
    s = MagicMock()
    s.path_exists.return_value = exists
    s.is_file.return_value = is_file
    s.download.return_value = payload
    s.exec_bash.return_value = {"exit_code": 0, "stdout": ""}
    return s


def test_collect_relative_success():
    session = make_fig_session()
    result = collect_declared_figure(
        session=session,
        workdir="/share",
        output_path="band.png",
        caption="Band structure",
        tool_call_id="call-1",
        upload_config=make_upload_config(),
    )
    assert isinstance(result, DeclaredFigureResult)
    assert result.failure_reason is None
    assert result.figure is not None
    assert result.figure.caption == "Band structure"
    assert result.figure.source_tool_call_id == "call-1"
    assert result.figure.asset_url == "https://assets.test/u/fig.png"
    assert result.figure_id.startswith("band-")
    assert result.resolved_path == "/share/band.png"
    assert result.figure.remote_path == "/share/band.png"


def test_collect_escape_returns_outside_workspace():
    result = collect_declared_figure(
        session=make_fig_session(),
        workdir="/share",
        output_path="../escape.png",
        caption="c",
        tool_call_id="call-1",
        upload_config=make_upload_config(),
    )
    assert result.figure is None
    assert result.failure_reason == "outside_workspace"
    assert result.guidance


def test_collect_missing_file_returns_file_not_found():
    result = collect_declared_figure(
        session=make_fig_session(exists=False),
        workdir="/share",
        output_path="band.png",
        caption="c",
        tool_call_id="call-1",
        upload_config=make_upload_config(),
    )
    assert result.failure_reason == "file_not_found"


def test_collect_directory_returns_not_a_file():
    result = collect_declared_figure(
        session=make_fig_session(is_file=False),
        workdir="/share",
        output_path="plots",
        caption="c",
        tool_call_id="call-1",
        upload_config=make_upload_config(),
    )
    assert result.failure_reason == "not_a_file"


def test_collect_non_image_returns_classification():
    result = collect_declared_figure(
        session=make_fig_session(payload=b"not an image"),
        workdir="/share",
        output_path="band.png",
        caption="c",
        tool_call_id="call-1",
        upload_config=make_upload_config(),
    )
    assert result.failure_reason == "image_header_mismatch"


def test_collect_download_failure_classified():
    session = make_fig_session()
    session.download.side_effect = RuntimeError("transport down")
    result = collect_declared_figure(
        session=session,
        workdir="/share",
        output_path="band.png",
        caption="c",
        tool_call_id="call-1",
        upload_config=make_upload_config(),
    )
    assert result.failure_reason == "download_failed"


def test_collect_upload_failure_classified():
    def boom(payload, key):
        raise RuntimeError("upload down")

    cfg = FigureUploadConfig(
        session_id="s", task_id="t", asset_key_prefix="figs", upload_bytes=boom
    )
    result = collect_declared_figure(
        session=make_fig_session(),
        workdir="/share",
        output_path="band.png",
        caption="c",
        tool_call_id="call-1",
        upload_config=cfg,
    )
    assert result.failure_reason == "upload_failed"
    assert result.figure_id is not None  # id is computed before upload
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/matmaster/tools/test_collect_declared_figure.py -k collect -v`
Expected: FAIL with `ImportError: cannot import name 'collect_declared_figure'`.

- [ ] **Step 3: Implement `DeclaredFigureResult`, guidance, and `collect_declared_figure`**

In `figure_artifacts.py`, add the result dataclass:

```python
@dataclass(slots=True)
class DeclaredFigureResult:
    figure: FigureDescriptor | None
    failure_reason: str | None
    guidance: str | None = None
    resolved_path: str | None = None
    figure_id: str | None = None
```

Add guidance text keyed by reason:

```python
def _declared_failure_guidance(reason: str, output_path: str) -> str:
    table = {
        "outside_workspace": (
            f"Expected image inside the workspace: {output_path}\n"
            "Provide an output_path that is absolute within the workspace "
            "or relative to the session workspace root."
        ),
        "file_not_found": (
            f"Expected image: {output_path}\n"
            "The command did not create this file. Re-run PlotFigure with the "
            "correct output_path, or publish an existing image by omitting command."
        ),
        "not_a_file": (
            f"Path is not a regular file: {output_path}\n"
            "Point output_path at an image file, not a directory."
        ),
        "unsupported_format": (
            f"Unsupported image format: {output_path}\n"
            "Use one of: .png, .jpg, .jpeg, .webp."
        ),
        "image_header_mismatch": (
            f"File contents are not a valid image or do not match the extension: "
            f"{output_path}\n"
            "Re-export the figure in a supported image format."
        ),
        "figure_too_large": (
            f"Image exceeds the size limit: {output_path}\n"
            "Reduce resolution or file size and retry."
        ),
        "download_failed": (
            f"Could not read the image from the session: {output_path}\n"
            "Retry PlotFigure; if it persists the session storage may be unavailable."
        ),
        "upload_failed": (
            f"Image was read but upload failed: {output_path}\n"
            "Retry PlotFigure; if it persists the asset backend may be unavailable."
        ),
    }
    return table.get(reason, f"Figure attachment failed for {output_path}.")
```

Add the collector:

```python
def collect_declared_figure(
    *,
    session: Session,
    workdir: str,
    output_path: str,
    caption: str,
    tool_call_id: str,
    upload_config: FigureUploadConfig,
) -> DeclaredFigureResult:
    """Resolve, validate, upload, and link one declared figure.

    Returns a DeclaredFigureResult with either a FigureDescriptor (success)
    or a stable failure_reason + actionable guidance. Never raises for
    expected failures.
    """

    def _fail(reason: str, *, resolved: str | None = None, figure_id: str | None = None):
        return DeclaredFigureResult(
            figure=None,
            failure_reason=reason,
            guidance=_declared_failure_guidance(reason, output_path),
            resolved_path=resolved,
            figure_id=figure_id,
        )

    resolved = resolve_workspace_output_path(raw_path=output_path, workdir=workdir)
    if resolved is None:
        return _fail("outside_workspace")
    if not session.path_exists(resolved):
        return _fail("file_not_found", resolved=resolved)
    if not session.is_file(resolved):
        return _fail("not_a_file", resolved=resolved)

    try:
        payload = _download_with_retry(session=session, path=resolved)
    except Exception:
        return _fail("download_failed", resolved=resolved)

    try:
        _validate_image_bytes(payload=payload, path=resolved)
    except FigureValidationError as exc:
        return _fail(exc.reason, resolved=resolved)

    figure_id = build_figure_id(output_path=output_path, image_bytes=payload)

    try:
        asset_key = _build_asset_key(
            upload_config=upload_config,
            tool_call_id=tool_call_id,
            figure_id=figure_id,
            source_path=resolved,
            payload=payload,
        )
        asset_url = _upload_with_retry(
            upload_bytes=upload_config.upload_bytes,
            payload=payload,
            asset_key=asset_key,
        )
    except Exception:
        return _fail("upload_failed", resolved=resolved, figure_id=figure_id)

    flat_dir = posixpath.join(
        posixpath.normpath(str(workdir)), ".matmaster", "figures"
    )
    _link_figure_flat(
        session=session,
        flat_dir=flat_dir,
        resolved_path=resolved,
        figure_id=figure_id,
    )

    return DeclaredFigureResult(
        figure=FigureDescriptor(
            figure_id=figure_id,
            asset_url=asset_url,
            caption=caption,
            source_tool_call_id=tool_call_id,
            remote_path=resolved,
        ),
        failure_reason=None,
        resolved_path=resolved,
        figure_id=figure_id,
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/matmaster/tools/test_collect_declared_figure.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/figure_artifacts.py tests/matmaster/tools/test_collect_declared_figure.py
git commit -m "feat: add collect_declared_figure pipeline"
```

---

## Chunk 3: PlotFigure tool

Build the model-visible tool on top of `collect_declared_figure` and `run_bash_command`, then register it.

### Task 8: `PlotFigure` metadata, schema, and `validate_input`

**Files:**
- Create: `matmaster/tools/builtin/plot_figure_tool.py`
- Test: `tests/matmaster/tools/builtin/test_plot_figure_tool.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/matmaster/tools/builtin/test_plot_figure_tool.py`:

```python
"""tests/matmaster/tools/builtin/test_plot_figure_tool.py"""

import asyncio

from matmaster.tools.builtin.plot_figure_tool import PlotFigure
from matmaster.types.topology import ToolPlane


def validate(tool, args):
    return asyncio.run(tool.validate_input(args))


class TestPlotFigureMetadata:
    def test_name(self):
        assert PlotFigure.name == "PlotFigure"

    def test_plane(self):
        assert PlotFigure.plane == ToolPlane.SESSION_SHELL

    def test_schema_requires_output_path_and_caption(self):
        assert PlotFigure.json_schema["required"] == ["output_path", "caption"]
        assert PlotFigure.json_schema["additionalProperties"] is False

    def test_has_prompt(self):
        assert "PlotFigure" in (PlotFigure(workdir="/share").prompt() or "")


class TestPlotFigureValidateInput:
    def test_missing_output_path_denied(self):
        tool = PlotFigure(workdir="/share")
        d = validate(tool, {"caption": "c"})
        assert d is not None and d.decision == "deny"

    def test_missing_caption_denied(self):
        tool = PlotFigure(workdir="/share")
        d = validate(tool, {"output_path": "band.png"})
        assert d is not None and d.decision == "deny"

    def test_empty_command_denied(self):
        tool = PlotFigure(workdir="/share")
        d = validate(tool, {"output_path": "band.png", "caption": "c", "command": "   "})
        assert d is not None and d.decision == "deny"

    def test_escape_output_path_denied(self):
        tool = PlotFigure(workdir="/share")
        d = validate(tool, {"output_path": "../x.png", "caption": "c"})
        assert d is not None and d.decision == "deny"

    def test_valid_relative_allowed(self):
        tool = PlotFigure(workdir="/share")
        d = validate(tool, {"output_path": "results/band.png", "caption": "c"})
        assert d is None

    def test_valid_with_command_allowed(self):
        tool = PlotFigure(workdir="/share")
        d = validate(tool, {"output_path": "band.png", "caption": "c", "command": "python p.py"})
        assert d is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/matmaster/tools/builtin/test_plot_figure_tool.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'matmaster.tools.builtin.plot_figure_tool'`.

- [ ] **Step 3: Create the tool skeleton with metadata + validate_input**

Create `matmaster/tools/builtin/plot_figure_tool.py`:

```python
"""matmaster/tools/builtin/plot_figure_tool.py

PlotFigure — generate or publish one figure and attach it to the response.
Single model-visible figure-publishing entry point. Two modes:
- with command: run the command, then collect output_path.
- without command: publish an already-existing image at output_path.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

from matmaster.tools.figure_artifacts import (
    collect_declared_figure,
    resolve_workspace_output_path,
)
from matmaster.tools.tool_result import ToolResult
from matmaster.types.figures import FigureUploadConfig
from matmaster.types.tool_decision import ToolDecision
from matmaster.types.tool_desc_ctx import ToolDescriptionContext
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ResourceClaim, ToolExecutionContext
from matmaster.types.topology import ToolPlane

from .base import BuiltinTool

_PLOT_TIMEOUT_CAP_MS = 600_000
_DEFAULT_TIMEOUT_MS = 120_000


class PlotFigure(BuiltinTool):
    name: ClassVar[str] = "PlotFigure"
    description: ClassVar[str] = (
        "Generate or publish one figure and attach it to the response."
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "command": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "Optional shell command to generate the figure. "
                    "Omit this when output_path already exists."
                ),
            },
            "output_path": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "Path to the image to attach. Absolute, or relative to "
                    "the session workspace."
                ),
            },
            "caption": {
                "type": "string",
                "minLength": 1,
                "description": "Caption shown with the figure in the response.",
            },
            "timeout": {
                "type": "integer",
                "minimum": 1,
                "maximum": 600000,
                "description": (
                    "Optional timeout in milliseconds for command execution. "
                    "Used only when command is provided. "
                    "Default 120000 (2 min), max 600000 (10 min)."
                ),
            },
        },
        "required": ["output_path", "caption"],
    }
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource="workspace", mode="exclusive"),
        ResourceClaim(resource="session", mode="exclusive"),
    )
    capabilities: ClassVar[frozenset[str]] = frozenset({"shell.execute"})
    effect_level: ClassVar[str] = "local_mutation"
    max_result_chars: ClassVar[int] = 30_000
    plane: ClassVar[ToolPlane] = ToolPlane.SESSION_SHELL

    def prompt(self, ctx: ToolDescriptionContext | None = None) -> str:
        return (
            "Use PlotFigure for any figure that should appear in the final answer.\n\n"
            "If you need to create the image now, provide command, output_path, and "
            "caption. If the image already exists from Bash, Bohrium, a skill, or a "
            "previous command, omit command and provide output_path and caption.\n\n"
            "Bash output is never shown as an answer image by itself. To show an "
            "existing image, publish it with PlotFigure. Write one figure per "
            "PlotFigure call; call it again for additional figures. After a "
            "successful call, reference the figure with the returned "
            "[[fig:<figure_id>]] marker."
        )

    async def validate_input(
        self,
        arguments: dict[str, Any],
        runner_state: ToolRunnerState | None = None,
    ) -> ToolDecision | None:
        output_path = arguments.get("output_path") or ""
        if not output_path.strip():
            return ToolDecision(decision="deny", reason="output_path is required")
        caption = arguments.get("caption") or ""
        if not caption.strip():
            return ToolDecision(decision="deny", reason="caption is required")
        if "command" in arguments:
            command = arguments.get("command") or ""
            if not command.strip():
                return ToolDecision(
                    decision="deny",
                    reason="command, when provided, must not be empty",
                )
        if self._workdir is None:
            return ToolDecision(decision="deny", reason="workdir not set")
        if (
            resolve_workspace_output_path(
                raw_path=output_path, workdir=str(self._workdir)
            )
            is None
        ):
            return ToolDecision(
                decision="deny",
                reason=f"output_path '{output_path}' is outside workspace boundary",
            )
        return None

    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        return ToolResult(
            status="error",
            content=(
                "PlotFigure requires execution context "
                "(figure upload config and tool_call_id)."
            ),
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/matmaster/tools/builtin/test_plot_figure_tool.py -v`
Expected: metadata + validate_input tests pass.

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/builtin/plot_figure_tool.py tests/matmaster/tools/builtin/test_plot_figure_tool.py
git commit -m "feat: add PlotFigure metadata and input validation"
```

### Task 9: `PlotFigure.execute_with_context` — no-command (publish existing)

**Files:**
- Modify: `matmaster/tools/builtin/plot_figure_tool.py`
- Test: `tests/matmaster/tools/builtin/test_plot_figure_tool.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/matmaster/tools/builtin/test_plot_figure_tool.py`:

```python
from unittest.mock import MagicMock

from matmaster.types.figures import FigureUploadConfig
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ToolExecutionContext

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def make_session(payload=_PNG):
    s = MagicMock()
    s.path_exists.return_value = True
    s.is_file.return_value = True
    s.download.return_value = payload
    s.exec_bash.return_value = {"exit_code": 0, "stdout": ""}
    return s


def make_upload_config(url="https://assets.test/u/fig.png"):
    return FigureUploadConfig(
        session_id="s", task_id="t", asset_key_prefix="figs",
        upload_bytes=lambda payload, key: url,
    )


def make_ctx(session, upload_config, tool_call_id="call-1"):
    state = ToolRunnerState()
    state.set("figure_upload_config", upload_config)
    return ToolExecutionContext(
        runner_state=state,
        tool_call_id=tool_call_id,
    )


def run_ctx(tool, args, ctx):
    return asyncio.run(tool.execute_with_context(args, ctx))


class TestPlotFigureNoCommand:
    def test_publishes_existing_image(self):
        session = make_session()
        tool = PlotFigure(session=session, workdir="/share")
        ctx = make_ctx(session, make_upload_config())
        result = run_ctx(tool, {"output_path": "results/band.png", "caption": "Band"}, ctx)
        assert result.status == "success"
        assert result.payload["figures"]
        fig = result.payload["figures"][0]
        assert fig["caption"] == "Band"
        assert f"[[fig:{fig['figure_id']}]]" in result.content
        assert fig["figure_id"] in result.content

    def test_does_not_exec_shell(self):
        session = make_session()
        tool = PlotFigure(session=session, workdir="/share")
        ctx = make_ctx(session, make_upload_config())
        run_ctx(tool, {"output_path": "band.png", "caption": "c"}, ctx)
        # The only exec_bash allowed is the flat-view symlink; never a user command.
        for call in session.exec_bash.call_args_list:
            cmd = call.kwargs.get("command") or (call.args[0] if call.args else "")
            assert "ln -s" in cmd or "mkdir -p" in cmd

    def test_missing_file_returns_error(self):
        session = make_session()
        session.path_exists.return_value = False
        tool = PlotFigure(session=session, workdir="/share")
        ctx = make_ctx(session, make_upload_config())
        result = run_ctx(tool, {"output_path": "band.png", "caption": "c"}, ctx)
        assert result.status == "error"
        assert "file_not_found" in result.content
        assert not result.payload.get("figures")

    def test_missing_upload_config_returns_error(self):
        session = make_session()
        tool = PlotFigure(session=session, workdir="/share")
        state = ToolRunnerState()
        ctx = ToolExecutionContext(runner_state=state, tool_call_id="call-1")
        result = run_ctx(tool, {"output_path": "band.png", "caption": "c"}, ctx)
        assert result.status == "error"
        assert "not configured" in result.content

    def test_missing_tool_call_id_returns_error(self):
        session = make_session()
        tool = PlotFigure(session=session, workdir="/share")
        ctx = make_ctx(session, make_upload_config(), tool_call_id=None)
        result = run_ctx(tool, {"output_path": "band.png", "caption": "c"}, ctx)
        assert result.status == "error"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/matmaster/tools/builtin/test_plot_figure_tool.py -k NoCommand -v`
Expected: FAIL — `execute_with_context` not overridden yet (base returns the `_execute` error, so assertions on `payload["figures"]` fail).

- [ ] **Step 3: Implement `execute_with_context`, `_resolve_figure_cfg`, `_run`, and `_assemble_result`**

Add these methods to `PlotFigure` (after `validate_input`). Note `run_bash_command` is imported lazily in `_run` to keep the no-command path free of bash-core imports at module load.

```python
    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        exec_ctx: ToolExecutionContext | None,
    ) -> str | ToolResult:
        try:
            figure_cfg: FigureUploadConfig | None = None
            tool_call_id: str | None = None
            if exec_ctx is not None:
                tool_call_id = exec_ctx.tool_call_id
                figure_cfg = self._resolve_figure_cfg(exec_ctx.runner_state)
            return await asyncio.to_thread(
                self._run, arguments, figure_cfg, tool_call_id
            )
        except Exception as exc:
            self.logger.error("Tool %s failed: %s", self.name, exc, exc_info=True)
            return f"Error: {exc}"

    def _resolve_figure_cfg(
        self, runner_state: ToolRunnerState | None
    ) -> FigureUploadConfig | None:
        if runner_state is None:
            return None
        raw = runner_state.get("figure_upload_config")
        if isinstance(raw, FigureUploadConfig):
            return raw
        if raw is None:
            return None
        try:
            return FigureUploadConfig.model_validate(raw)
        except Exception:
            self.logger.warning("Ignoring invalid figure_upload_config for %s", self.name)
            return None

    def _run(
        self,
        arguments: dict[str, Any],
        figure_cfg: FigureUploadConfig | None,
        tool_call_id: str | None,
    ) -> str | ToolResult:
        session = self._require_session()
        if figure_cfg is None:
            return ToolResult(
                status="error",
                content="Figure attachment failed: figure upload is not configured for this run.",
            )
        if not tool_call_id:
            return ToolResult(
                status="error",
                content="Figure attachment failed: missing tool_call_id for this run.",
            )

        output_path: str = arguments["output_path"]
        caption: str = arguments["caption"]
        command: str = (arguments.get("command") or "").strip()
        workdir = str(self._workdir)

        observation = ""
        exit_code = 0
        if command:
            from matmaster.tools.bash_runner import run_bash_command

            timeout_ms = int(arguments.get("timeout", _DEFAULT_TIMEOUT_MS))
            timeout_s = min(timeout_ms, _PLOT_TIMEOUT_CAP_MS) / 1000
            run = run_bash_command(
                session=session,
                command=command,
                timeout_s=timeout_s,
                cancel_token=self._cancel_token_for_exec(),
            )
            observation = run.observation
            exit_code = run.exit_code

        declared = collect_declared_figure(
            session=session,
            workdir=workdir,
            output_path=output_path,
            caption=caption,
            tool_call_id=tool_call_id,
            upload_config=figure_cfg,
        )
        return self._assemble_result(
            observation=observation,
            exit_code=exit_code,
            has_command=bool(command),
            declared=declared,
            output_path=output_path,
            caption=caption,
        )

    def _assemble_result(
        self,
        *,
        observation: str,
        exit_code: int,
        has_command: bool,
        declared: Any,
        output_path: str,
        caption: str,
    ) -> ToolResult:
        if declared.figure is not None:
            success_block = (
                "Figure attached:\n"
                f"- figure_id: {declared.figure_id}\n"
                f"- path: {output_path}\n"
                f"- caption: {caption}\n"
                f"Use [[fig:{declared.figure_id}]] when referring to this figure."
            )
            content = (
                f"{observation}\n{success_block}" if has_command else success_block
            )
            status = "error" if (has_command and exit_code != 0) else "success"
            return ToolResult(
                status=status,
                content=content,
                payload={"figures": [declared.figure.model_dump(mode="json")]},
            )

        failure_block = (
            f"Figure attachment failed: {declared.failure_reason}\n"
            f"{declared.guidance or ''}"
        ).rstrip()
        content = (
            f"{observation}\n{failure_block}" if has_command else failure_block
        )
        return ToolResult(status="error", content=content)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/matmaster/tools/builtin/test_plot_figure_tool.py -k NoCommand -v`
Expected: all no-command tests pass.

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/builtin/plot_figure_tool.py tests/matmaster/tools/builtin/test_plot_figure_tool.py
git commit -m "feat: implement PlotFigure publish-existing mode"
```

### Task 10: `PlotFigure` with-command mode and status matrix

**Files:**
- Modify: `tests/matmaster/tools/builtin/test_plot_figure_tool.py` only (logic already implemented in Task 9)
- Test: same file

- [ ] **Step 1: Write the failing tests for the command path**

Append to `tests/matmaster/tools/builtin/test_plot_figure_tool.py`:

```python
def make_cmd_session(exit_code=0, output="done", payload=_PNG, file_after=True):
    s = MagicMock()
    s.exec_bash.return_value = {
        "output": output,
        "exit_code": exit_code,
        "working_dir": "/share",
        "stdout": "",
    }
    s.path_exists.return_value = file_after
    s.is_file.return_value = True
    s.download.return_value = payload
    return s


class TestPlotFigureWithCommand:
    def test_command_success_and_figure(self):
        session = make_cmd_session(exit_code=0)
        tool = PlotFigure(session=session, workdir="/share")
        ctx = make_ctx(session, make_upload_config())
        result = run_ctx(
            tool,
            {"command": "python plot.py", "output_path": "xrd.png", "caption": "XRD"},
            ctx,
        )
        assert result.status == "success"
        assert result.payload["figures"]
        assert "[Command finished with exit code 0]" in result.content
        assert "[[fig:" in result.content

    def test_command_fails_but_figure_collected(self):
        session = make_cmd_session(exit_code=1, file_after=True)
        tool = PlotFigure(session=session, workdir="/share")
        ctx = make_ctx(session, make_upload_config())
        result = run_ctx(
            tool,
            {"command": "python plot.py", "output_path": "xrd.png", "caption": "XRD"},
            ctx,
        )
        assert result.status == "error"
        assert result.payload["figures"]  # figure survives a failed command
        assert "[Command finished with exit code 1]" in result.content

    def test_command_succeeds_but_no_figure(self):
        session = make_cmd_session(exit_code=0, file_after=False)
        tool = PlotFigure(session=session, workdir="/share")
        ctx = make_ctx(session, make_upload_config())
        result = run_ctx(
            tool,
            {"command": "python plot.py", "output_path": "xrd.png", "caption": "XRD"},
            ctx,
        )
        assert result.status == "error"
        assert not result.payload.get("figures")
        assert "file_not_found" in result.content
```

- [ ] **Step 2: Run to verify it passes (logic already present)**

Run: `uv run pytest tests/matmaster/tools/builtin/test_plot_figure_tool.py -k WithCommand -v`
Expected: all three pass. (If any fail, fix `_assemble_result`/`_run` until green — do not weaken the assertions.)

- [ ] **Step 3: Run the whole PlotFigure suite**

Run: `uv run pytest tests/matmaster/tools/builtin/test_plot_figure_tool.py -v`
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/matmaster/tools/builtin/test_plot_figure_tool.py
git commit -m "test: cover PlotFigure command status matrix"
```

### Task 11: Register `PlotFigure`

**Files:**
- Modify: `matmaster/tools/builtin/__init__.py`
- Modify: `matmaster/core/exp.py:93-107` (`_SESSION_REQUIRING_TOOL_NAMES`), `exp.py:664-668` (docstring), `exp.py:679-719` (imports + `session_tools`)
- Modify: `matmaster/exps/direct.toml`
- Modify: `matmaster/exps/planner.toml`
- Test: `tests/matmaster/tools/builtin/test_plot_figure_tool.py`
- Test: `tests/matmaster/core/test_exp.py`

- [ ] **Step 1: Write the failing registration test**

Append to `tests/matmaster/tools/builtin/test_plot_figure_tool.py`:

```python
def test_exported_from_builtin_package():
    from matmaster.tools.builtin import PlotFigure as Exported

    assert Exported is PlotFigure


def test_in_session_requiring_names():
    from matmaster.core.exp import _SESSION_REQUIRING_TOOL_NAMES

    assert "PlotFigure" in _SESSION_REQUIRING_TOOL_NAMES
```

In `tests/matmaster/core/test_exp.py`, add the loader import near the top:

```python
from matmaster.config.loader import load_exp_config
```

Then update the existing builtin registration expectations and add the config exposure test:

```python
    def test_native_tools_count(self, tmp_path: Path) -> None:
        """12 native tools registered with source='builtin' (CC names)."""
        _, registry = self._build_registry(tmp_path)
        assert len(registry) == 12

    def test_native_tool_names(self, tmp_path: Path) -> None:
        """All 12 expected CC-name tools are present in registry."""
        _, registry = self._build_registry(tmp_path)
        expected_native = {
            "AskQuestion",
            "Bash",
            "PlotFigure",
            "Read",
            "Write",
            "Edit",
            "Glob",
            "Grep",
            "TodoWrite",
            "WebSearch",
            "WebFetch",
            "Bohrium",
        }
        for name in expected_native:
            assert name in registry, f"Expected tool '{name}' not found in registry"

    def test_total_count(self, tmp_path: Path) -> None:
        """Total tools = 12 native builtin (CC names, no legacy tools)."""
        _, registry = self._build_registry(tmp_path)
        assert len(registry) == 12

    def test_direct_and_planner_configs_include_plot_figure(self) -> None:
        assert "PlotFigure" in load_exp_config("direct").tools.builtin
        assert "PlotFigure" in load_exp_config("planner").tools.builtin
```

- [ ] **Step 2: Run to verify it fails**

Run:
```bash
uv run pytest tests/matmaster/tools/builtin/test_plot_figure_tool.py -k "exported or session_requiring" -v
uv run pytest tests/matmaster/core/test_exp.py -k "native_tools_count or native_tool_names or total_count or direct_and_planner_configs_include_plot_figure" -v
```
Expected: FAIL on the `PlotFigure` import / membership assertions, native tool count/set assertions, and direct/planner config exposure assertion.

- [ ] **Step 3: Wire registration**

In `matmaster/tools/builtin/__init__.py`, add the import and `__all__` entry (alphabetical with the rest):

```python
from matmaster.tools.builtin.plot_figure_tool import PlotFigure
```

and add `"PlotFigure",` to `__all__`.

In `matmaster/core/exp.py`:
- Add `"PlotFigure",` to the `_SESSION_REQUIRING_TOOL_NAMES` frozenset (lines 93-107).
- Add `PlotFigure` to the builtin import block (lines 679-691).
- Add the instance to `session_tools` (after `BashTool`, lines 704-719):

```python
                PlotFigure(session=env.session, workdir=exec_wd),
```

- Update the docstring tool list (line 664) to include `PlotFigure` among the session-requiring tools.

In `matmaster/exps/direct.toml`, add `"PlotFigure",` immediately after `"Bash",` in the `[tools].builtin` list.

In `matmaster/exps/planner.toml`, add `"PlotFigure",` immediately after `"Bash",` in the `[tools].builtin` list.

- [ ] **Step 4: Run registration tests + a broad import smoke check**

Run:
```bash
uv run pytest tests/matmaster/tools/builtin/test_plot_figure_tool.py tests/matmaster/core/test_exp.py tests/matmaster/integration/test_direct_toml_prompt.py -v
uv run python -c "import matmaster.core.exp; from matmaster.config.loader import load_exp_config; assert 'PlotFigure' in load_exp_config('direct').tools.builtin; assert 'PlotFigure' in load_exp_config('planner').tools.builtin"
```
Expected: tests pass; import has no errors; direct and planner configs expose `PlotFigure`.

- [ ] **Step 5: Commit**

```bash
git add matmaster/tools/builtin/__init__.py matmaster/core/exp.py matmaster/exps/direct.toml matmaster/exps/planner.toml tests/matmaster/tools/builtin/test_plot_figure_tool.py tests/matmaster/core/test_exp.py
git commit -m "feat: register PlotFigure as a session tool"
```

---

## Chunk 4: Decommission the old manifest chain

`PlotFigure` now owns figure publishing. Strip figure logic from `BashTool`, delete the manifest pipeline, drop `FigureManifestEntry`, and migrate the prompt and tests. No compatibility shims.

### Task 12: Strip figure logic from `BashTool`

**Files:**
- Modify: `matmaster/tools/builtin/bash_tool.py`
- Modify: `tests/matmaster/tools/builtin/test_bash_tool.py`

- [ ] **Step 1: Remove figure imports, env, collection, and manifest prompt**

In `bash_tool.py`:
- Delete the import block `from matmaster.tools.figure_artifacts import (build_figure_env, collect_figures_from_session)` and `from matmaster.types.figures import FigureUploadConfig`, plus the now-unused `shlex` and `ValidationError` imports. KEEP the top-level `from matmaster.tools.bash_runner import run_bash_command` added in Chunk 1 — the new `_execute` uses it.
- Delete the manifest paragraph from `prompt()` (lines 114-118), ending the prompt after the turn-economy sentence.
- Simplify `execute_with_context` to stop reading `figure_upload_config`/`tool_call_id`:

```python
    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        exec_ctx: ToolExecutionContext | None,
    ) -> str | ToolResult:
        try:
            return await asyncio.to_thread(self._execute, arguments)
        except Exception as e:
            self.logger.error("Tool %s failed: %s", self.name, e, exc_info=True)
            return f"Error: {e}"
```

- Replace `_execute` + `_execute_with_figure_support` with a single plain executor that uses the **top-level** `run_bash_command` import (do NOT add a function-local import — it would leave the top-level one dead):

```python
    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        session = self._require_session()
        command: str = (arguments.get("command") or "").strip()
        if not command:
            return "Error: command is required and must not be empty."

        timeout_ms = int(arguments.get("timeout", 120_000))
        cap = (
            _SLEEP_TIMEOUT_CAP_MS
            if _PURE_SLEEP_RE.fullmatch(command)
            else _GENERAL_TIMEOUT_CAP_MS
        )
        timeout_s = min(timeout_ms, cap) / 1000

        run = run_bash_command(
            session=session,
            command=command,
            timeout_s=timeout_s,
            cancel_token=self._cancel_token_for_exec(),
        )
        if run.exit_code != 0:
            return ToolResult(status="error", content=run.observation)
        return run.observation
```

- [ ] **Step 2: Migrate the BashTool test file**

In `tests/matmaster/tools/builtin/test_bash_tool.py`:
- Remove the import `from matmaster.tools.figure_artifacts import FigureCollectionResult, build_figure_env` (line 10) and `from matmaster.types.figures import FigureUploadConfig` (line 14) if the remaining tests no longer need them.
- Delete every test that asserts `ARTIFACT_DIR`/`MANIFEST_PATH` env injection, manifest collection, or `payload["figures"]` from `BashTool`. (Search the file for `ARTIFACT_DIR`, `MANIFEST_PATH`, `figure`, `manifest`.)
- Keep all pure command-execution, timeout-cap, sleep-exception, and metadata tests.

- [ ] **Step 3: Verify BashTool behavior + clean imports**

Run: `uv run pytest tests/matmaster/tools/builtin/test_bash_tool.py -v`
Then: `uv run python -c "import matmaster.tools.builtin.bash_tool"`
Expected: tests pass; import succeeds. Confirm by inspection that no figure imports (`build_figure_env`, `collect_figures_from_session`, `FigureUploadConfig`), `shlex`, or `ValidationError` remain, and that the top-level `run_bash_command` import is still present and used.

- [ ] **Step 4: Commit**

```bash
git add matmaster/tools/builtin/bash_tool.py tests/matmaster/tools/builtin/test_bash_tool.py
git commit -m "refactor: remove figure logic from BashTool"
```

### Task 13: Delete the manifest pipeline from `figure_artifacts.py`

**Files:**
- Modify: `matmaster/tools/figure_artifacts.py`
- Modify: `tests/matmaster/tools/test_figure_artifacts.py`, `tests/matmaster/tools/test_figure_artifacts_real_fs.py`

- [ ] **Step 1: Delete the dead symbols**

In `figure_artifacts.py`, delete: `build_figure_env`, `collect_figures_from_session`, `_load_manifest`, `_ManifestLoadResult`, `FigureCollectionResult`, `_resolve_artifact_path`, and the thin `_link_figure_into_flat_view` wrapper added in Task 6 (its only caller `collect_figures_from_session` is now gone). Keep `_link_figure_flat`. Remove the now-unused `FigureManifestEntry` import (the `json` import too if unreferenced).

- [ ] **Step 2: Migrate the figure_artifacts tests**

Replace manifest-oriented cases in `tests/matmaster/tools/test_figure_artifacts.py` and `tests/matmaster/tools/test_figure_artifacts_real_fs.py`:
- Delete tests that import or call `build_figure_env`, `collect_figures_from_session`, `_load_manifest`, `_resolve_artifact_path`, `FigureCollectionResult`.
- Keep tests for the still-public reused helpers (`_validate_image_bytes`, `_sniff_image_format`, `_build_asset_key`, `_sanitize_key_segment`, `_download_with_retry`, `_upload_with_retry`). If a real-fs test exercised the end-to-end flat-view via the old path, re-point it at `collect_declared_figure` (the new behavior already has coverage in `test_collect_declared_figure.py`; only port what adds real-fs value, otherwise delete).

- [ ] **Step 3: Verify no remaining references repo-wide**

Run: `rg -n "build_figure_env|collect_figures_from_session|_load_manifest|_ManifestLoadResult|_resolve_artifact_path|FigureCollectionResult" matmaster src tests`
Expected: no matches.
Then: `uv run pytest tests/matmaster/tools/test_figure_artifacts.py tests/matmaster/tools/test_figure_artifacts_real_fs.py tests/matmaster/tools/test_collect_declared_figure.py -v`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add matmaster/tools/figure_artifacts.py tests/matmaster/tools/test_figure_artifacts.py tests/matmaster/tools/test_figure_artifacts_real_fs.py
git commit -m "refactor: delete manifest figure pipeline"
```

### Task 14: Delete `FigureManifestEntry`

**Files:**
- Modify: `matmaster/types/figures.py:11-21`
- Modify: `matmaster/types/__init__.py`
- Modify: `tests/matmaster/types/test_figures.py`

- [ ] **Step 1: Remove the type and its export**

- Delete the `FigureManifestEntry` class from `matmaster/types/figures.py` (lines 11-21).
- Remove `FigureManifestEntry` from the import and `__all__` in `matmaster/types/__init__.py`.
- In `tests/matmaster/types/test_figures.py`: remove `FigureManifestEntry` from the top-of-file import AND delete its test case(s); keep `FigureDescriptor` / `FigureUploadConfig` coverage. The file must still collect without ImportError.

- [ ] **Step 2: Verify no remaining references**

Run: `rg -n "FigureManifestEntry" matmaster src tests`
Expected: no matches.
Then: `uv run pytest tests/matmaster/types/test_figures.py -v`
Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add matmaster/types/figures.py matmaster/types/__init__.py tests/matmaster/types/test_figures.py
git commit -m "refactor: delete FigureManifestEntry type"
```

### Task 15: Prompt migration in `_base.toml`

**Files:**
- Modify: `matmaster/exps/_base.toml`

- [ ] **Step 1: Update the figure guidance**

Extend the figure-related guidance (around `_base.toml:46-47`) so it covers PlotFigure. Keep the existing `[[fig:<figure_id>]]` lines and add:

```toml
 - Figures for the final answer must be published with the PlotFigure tool.
 - To create and publish at once, call PlotFigure with command, output_path, and caption.
 - To publish an image already produced by Bash, Bohrium, a skill, or a previous command, call PlotFigure with output_path and caption (no command).
 - Bash alone never shows an image in the answer; only PlotFigure does.
 - Use the figure_id returned by PlotFigure when writing [[fig:<figure_id>]].
```

- [ ] **Step 2: Verify the toml parses**

Run: `uv run python -c "import tomllib; tomllib.load(open('matmaster/exps/_base.toml','rb'))"`
Expected: no error.

- [ ] **Step 3: Commit**

```bash
git add matmaster/exps/_base.toml
git commit -m "docs: migrate figure prompt guidance to PlotFigure"
```

---

## Chunk 5: End-to-end aggregation verification

Prove both `PlotFigure` modes flow through the unchanged aggregation chain into `ResponseFiguresEvent`, including the error-status-with-figures path, child promotion, and first-writer-wins dedup.

### Task 16: End-to-end response-figures aggregation tests

**Files:**
- Create: `tests/matmaster/services/test_plot_figure_aggregation.py`

- [ ] **Step 1: Write the end-to-end tests**

Create `tests/matmaster/services/test_plot_figure_aggregation.py`. These drive `PlotFigure` -> `ToolResult`, lift it into a `ToolResultEvent` the same way the kernel's tool-dispatch does (status and payload set as independent kwargs — re-read `agent_tool_dispatch.py` for the exact construction; it is under active change, so do not rely on a line number), and assert the accumulator emits a snapshot.

```python
"""tests/matmaster/services/test_plot_figure_aggregation.py"""

import asyncio
from unittest.mock import MagicMock

from matmaster.tools.builtin.plot_figure_tool import PlotFigure
from matmaster.types.events import ToolResultEvent
from matmaster.types.figures import FigureUploadConfig
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ToolExecutionContext
from src.services.response_figures_service import ResponseFiguresAccumulator

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def make_session(payload=_PNG, exit_code=0, file_after=True):
    s = MagicMock()
    s.exec_bash.return_value = {
        "output": "done", "exit_code": exit_code, "working_dir": "/share", "stdout": "",
    }
    s.path_exists.return_value = file_after
    s.is_file.return_value = True
    s.download.return_value = payload
    return s


def make_upload_config(url):
    return FigureUploadConfig(
        session_id="s", task_id="t", asset_key_prefix="figs",
        upload_bytes=lambda payload, key: url,
    )


def publish(tool, args, session, url, tool_call_id):
    state = ToolRunnerState()
    state.set("figure_upload_config", make_upload_config(url))
    ctx = ToolExecutionContext(runner_state=state, tool_call_id=tool_call_id)
    result = asyncio.run(tool.execute_with_context(args, ctx))
    # Mirror agent_tool_dispatch: status and payload set independently.
    return ToolResultEvent(
        source="agent",
        call_id=tool_call_id,
        tool_name="PlotFigure",
        result=result.content,
        status=result.status,
        payload=result.payload,
    )


def test_no_command_publish_reaches_snapshot():
    session = make_session()
    tool = PlotFigure(session=session, workdir="/share")
    event = publish(tool, {"output_path": "band.png", "caption": "Band"}, session,
                    "https://a/1.png", "call-1")
    acc = ResponseFiguresAccumulator()
    assert acc.add_tool_result(event) is True
    snap = acc.build_snapshot_event_if_dirty()
    assert snap is not None
    assert len(snap.figures) == 1


def test_command_mode_reaches_snapshot():
    session = make_session(exit_code=0)
    tool = PlotFigure(session=session, workdir="/share")
    event = publish(tool, {"command": "python p.py", "output_path": "xrd.png", "caption": "XRD"},
                    session, "https://a/2.png", "call-2")
    assert event.status == "success"
    acc = ResponseFiguresAccumulator()
    assert acc.add_tool_result(event) is True
    snap = acc.build_snapshot_event_if_dirty()
    assert snap is not None and len(snap.figures) == 1


def test_failed_command_with_figure_still_aggregates():
    session = make_session(exit_code=1, file_after=True)
    tool = PlotFigure(session=session, workdir="/share")
    event = publish(tool, {"command": "python p.py", "output_path": "xrd.png", "caption": "XRD"},
                    session, "https://a/3.png", "call-3")
    assert event.status == "error"
    acc = ResponseFiguresAccumulator()
    assert acc.add_tool_result(event) is True  # error status, payload still ingested


def test_multiple_publishes_build_incremental_snapshots():
    acc = ResponseFiguresAccumulator()
    s1 = make_session()
    e1 = publish(PlotFigure(session=s1, workdir="/share"),
                 {"output_path": "a.png", "caption": "A"}, s1, "https://a/a.png", "call-a")
    acc.add_tool_result(e1)
    snap1 = acc.build_snapshot_event_if_dirty()
    acc.mark_snapshot_emitted()
    s2 = make_session()
    e2 = publish(PlotFigure(session=s2, workdir="/share"),
                 {"output_path": "b.png", "caption": "B"}, s2, "https://a/b.png", "call-b")
    acc.add_tool_result(e2)
    snap2 = acc.build_snapshot_event_if_dirty()
    assert len(snap1.figures) == 1
    assert len(snap2.figures) == 2
    assert snap1.figures[0].figure_id == snap2.figures[0].figure_id


def test_duplicate_figure_id_first_writer_wins():
    acc = ResponseFiguresAccumulator()
    # Same bytes + same output_path basename -> identical figure_id.
    s1 = make_session()
    e1 = publish(PlotFigure(session=s1, workdir="/share"),
                 {"output_path": "dup.png", "caption": "first"}, s1, "https://a/x.png", "call-x")
    s2 = make_session()
    e2 = publish(PlotFigure(session=s2, workdir="/share"),
                 {"output_path": "dup.png", "caption": "second"}, s2, "https://a/y.png", "call-y")
    assert acc.add_tool_result(e1) is True
    assert acc.add_tool_result(e2) is False  # duplicate id ignored
    snap = acc.build_snapshot_event_if_dirty()
    assert len(snap.figures) == 1
    assert snap.figures[0].caption == "first"


def test_child_spawn_figure_promotes_only_with_include_spawned():
    # A child agent's PlotFigure result carries spawn_id. The accumulator gates
    # it out by default and promotes it only when include_spawned=True — exactly
    # what FigureCoordinator.child_event_sink passes. Assert the accumulator
    # boundary here without touching FigureCoordinator.
    session = make_session()
    tool = PlotFigure(session=session, workdir="/share")
    state = ToolRunnerState()
    state.set("figure_upload_config", make_upload_config("https://a/child.png"))
    ctx = ToolExecutionContext(runner_state=state, tool_call_id="call-child")
    result = asyncio.run(
        tool.execute_with_context({"output_path": "child.png", "caption": "Child"}, ctx)
    )
    event = ToolResultEvent(
        source="agent",
        call_id="call-child",
        tool_name="PlotFigure",
        result=result.content,
        status=result.status,
        payload=result.payload,
        spawn_id="child-1",
    )
    acc = ResponseFiguresAccumulator()
    assert acc.add_tool_result(event) is False  # gated: spawn_id set, not included
    assert acc.add_tool_result(event, include_spawned=True) is True  # promoted
```

- [ ] **Step 2: Run the end-to-end suite**

Run: `uv run pytest tests/matmaster/services/test_plot_figure_aggregation.py -v`
Expected: all five tests pass.

- [ ] **Step 3: Run the full figure-related test set as a regression gate**

Run:
```bash
uv run pytest \
  tests/matmaster/tools/test_bash_runner.py \
  tests/matmaster/tools/test_collect_declared_figure.py \
  tests/matmaster/tools/builtin/test_plot_figure_tool.py \
  tests/matmaster/tools/builtin/test_bash_tool.py \
  tests/matmaster/tools/test_figure_artifacts.py \
  tests/matmaster/tools/test_figure_artifacts_real_fs.py \
  tests/matmaster/types/test_figures.py \
  tests/matmaster/core/test_exp.py \
  tests/matmaster/services/test_plot_figure_aggregation.py \
  tests/matmaster/services/test_response_figures_service.py \
  -v
```
Expected: all pass, no skips related to figures.

- [ ] **Step 4: Commit**

```bash
git add tests/matmaster/services/test_plot_figure_aggregation.py
git commit -m "test: end-to-end PlotFigure response-figures aggregation"
```

---

## Acceptance Criteria (from spec §14)

- [ ] Exactly one new model-visible builtin tool: `PlotFigure`.
- [ ] `PlotFigure(command, output_path, caption)` runs the command and publishes one figure.
- [ ] `PlotFigure(output_path, caption)` publishes an existing workspace image and runs no shell.
- [ ] `output_path` accepts absolute and workspace-relative paths; escapes are denied at `validate_input`.
- [ ] Non-zero command exit still attempts collection; if the figure exists, payload still carries figures.
- [ ] Success `ToolResult.content` includes `figure_id` and the `[[fig:<figure_id>]]` hint.
- [ ] `payload["figures"]` uses the existing `FigureDescriptor` shape.
- [ ] `figure_id` is auto-generated, stable, sanitized, length-bounded.
- [ ] `BashTool` contains no figure manifest / artifact env / upload logic.
- [ ] The manifest chain is deleted with no compatibility residue or import breakage.
- [ ] The flat symlink view persists, now workdir-based.
- [ ] Downstream aggregation is unchanged; end-to-end tests prove both modes emit `ResponseFiguresEvent`.

## Final Verification

- [ ] `uv run pytest --collect-only -q` succeeds with no ImportError/NameError (this project has no ruff; collection is the import gate).
- [ ] `rg -n "FigureManifestEntry|build_figure_env|collect_figures_from_session|_load_manifest|_resolve_artifact_path|FigureCollectionResult" matmaster src tests` returns nothing.
- [ ] `uv run pytest tests/matmaster/core/test_exp.py tests/matmaster/integration/test_direct_toml_prompt.py -v` confirms `PlotFigure` registration and direct/planner config exposure.
- [ ] Full suite for touched areas green (Task 16 Step 3 command).
