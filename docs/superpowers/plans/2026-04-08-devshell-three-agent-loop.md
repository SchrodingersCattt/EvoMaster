# DevShell Three-Agent Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the DevShell self-iteration orchestrator into a strict three-agent architecture with explicit optimization delegation, evaluation isolation, and persistent cross-run iteration summaries.

**Architecture:** Extend the existing checklist follow-up pattern so the outer loop owns three distinct roles: a drive-only main agent, an evaluation-only checklist agent, and a product-only optimization agent. Enforce separation through per-agent tool allowlists, path-filtered prompts and payloads, new MCP delegation/report tools, and a persistent history writer outside `results/`.

**Tech Stack:** Python 3.11+, Claude Agent SDK integration, pytest, JSONL/JSON session artifacts

---

### Task 1: Add Failing Tests For Shared State And MCP Tooling

**Files:**
- Modify: `tests/evaluation/test_devshell_agent_sdk_tools.py`
- Modify: `evaluation/devshell_agent/config_state.py`
- Modify: `evaluation/devshell_agent/sdk_tools.py`

- [ ] **Step 1: Write the failing test for optimization delegation state**

```python
def test_delegate_optimization_records_round_and_payload(tmp_path: Path) -> None:
    state = _build_state(tmp_path)
    toolkit_cls = _sdk_tools_module().MatmasterEvalMcpToolkit
    toolkit = toolkit_cls(state)

    result = asyncio.run(
        toolkit._delegate_optimization(
            {
                "iteration_index": 1,
                "problem_summary": "Need stronger reusable workflow guidance.",
                "symptom": "Low score due to missing deliverable structure.",
                "suggested_focus": ["matmaster/skills"],
                "allowed_evidence_paths": ["matmaster/skills/result-analysis/SKILL.md"],
                "notes": "Do not expose raw rubric text.",
            }
        )
    )

    assert result["is_error"] is False
    assert state.optimization_delegations_pending == [
        {
            "iteration_index": 1,
            "optimization_round": 1,
            "problem_summary": "Need stronger reusable workflow guidance.",
            "symptom": "Low score due to missing deliverable structure.",
            "suggested_focus": ["matmaster/skills"],
            "allowed_evidence_paths": ["matmaster/skills/result-analysis/SKILL.md"],
            "notes": "Do not expose raw rubric text.",
        }
    ]
```

- [ ] **Step 2: Run the single test to verify it fails**

Run: `uv run pytest tests/evaluation/test_devshell_agent_sdk_tools.py::test_delegate_optimization_records_round_and_payload -v`

Expected: FAIL with missing optimization state fields or missing `_delegate_optimization`.

- [ ] **Step 3: Write the failing test for optimization report recording**

```python
def test_report_optimization_result_persists_jsonl(tmp_path: Path) -> None:
    state = _build_state(tmp_path)
    toolkit_cls = _sdk_tools_module().MatmasterEvalMcpToolkit
    toolkit = toolkit_cls(state)

    asyncio.run(
        toolkit._report_optimization_result(
            {
                "iteration_index": 1,
                "optimization_round": 2,
                "summary": "Updated reusable skill instructions.",
                "files_touched": ["matmaster/skills/demo/SKILL.md"],
                "commit_shas": ["abc1234"],
                "needs_more_work": False,
                "followup_suggestion": "Re-run eval.",
            }
        )
    )

    assert state.optimization_reports == [
        {
            "iteration_index": 1,
            "optimization_round": 2,
            "summary": "Updated reusable skill instructions.",
            "files_touched": ["matmaster/skills/demo/SKILL.md"],
            "commit_shas": ["abc1234"],
            "needs_more_work": False,
            "followup_suggestion": "Re-run eval.",
        }
    ]
    log_path = tmp_path / "session" / "optimization_reports.jsonl"
    assert log_path.is_file()
```

- [ ] **Step 4: Run the single test to verify it fails**

Run: `uv run pytest tests/evaluation/test_devshell_agent_sdk_tools.py::test_report_optimization_result_persists_jsonl -v`

Expected: FAIL because `_report_optimization_result` or JSONL logging does not exist.

- [ ] **Step 5: Implement minimal shared-state and MCP-tool support**

```python
@dataclass
class AgentLoopSharedState:
    # existing fields ...
    optimization_delegations_pending: list[dict[str, Any]] = field(default_factory=list)
    optimization_reports: list[dict[str, Any]] = field(default_factory=list)
    optimization_rounds_by_iteration: dict[int, int] = field(default_factory=dict)
```

```python
class MatmasterEvalMcpToolkit:
    DELEGATE_OPTIMIZATION_SCHEMA = {
        "type": "object",
        "properties": {
            "iteration_index": {"type": "integer"},
            "problem_summary": {"type": "string"},
            "symptom": {"type": "string"},
            "suggested_focus": {"type": "array", "items": {"type": "string"}},
            "allowed_evidence_paths": {"type": "array", "items": {"type": "string"}},
            "notes": {"type": "string"},
        },
        "required": [
            "iteration_index",
            "problem_summary",
            "symptom",
            "suggested_focus",
            "allowed_evidence_paths",
            "notes",
        ],
    }
```

```python
async def _delegate_optimization(self, args: dict[str, Any]) -> dict[str, Any]:
    iteration_index = int(args["iteration_index"])
    round_index = self._state.optimization_rounds_by_iteration.get(iteration_index, 0) + 1
    self._state.optimization_rounds_by_iteration[iteration_index] = round_index
    row = {
        "iteration_index": iteration_index,
        "optimization_round": round_index,
        "problem_summary": str(args["problem_summary"]),
        "symptom": str(args["symptom"]),
        "suggested_focus": list(args["suggested_focus"]),
        "allowed_evidence_paths": list(args["allowed_evidence_paths"]),
        "notes": str(args["notes"]),
    }
    self._state.optimization_delegations_pending.append(row)
    self._append_optimization_delegation_jsonl(row)
    return {"content": [{"type": "text", "text": "queued"}], "is_error": False}
```

```python
async def _report_optimization_result(self, args: dict[str, Any]) -> dict[str, Any]:
    row = dict(args)
    self._state.optimization_reports.append(row)
    self._append_optimization_report_jsonl(row)
    return {"content": [{"type": "text", "text": "recorded"}]}
```

- [ ] **Step 6: Run the focused test file to verify it passes**

Run: `uv run pytest tests/evaluation/test_devshell_agent_sdk_tools.py -v`

Expected: PASS for the new optimization tests and existing ingest-submit tests.

- [ ] **Step 7: Commit the tooling state change**

```bash
git add tests/evaluation/test_devshell_agent_sdk_tools.py evaluation/devshell_agent/config_state.py evaluation/devshell_agent/sdk_tools.py
git commit -m "feat: add optimization delegation state to devshell loop"
```

### Task 2: Add Failing Tests For Three-Agent Loop Control Flow

**Files:**
- Modify: `tests/evaluation/test_devshell_agent_sdk_tools.py`
- Modify: `evaluation/devshell_agent/loop.py`

- [ ] **Step 1: Write the failing test for main-agent tool restrictions**

```python
def test_main_agent_allowed_tools_exclude_edit_write_and_bash() -> None:
    allowed = DevshellAgentLoop.main_agent_allowed_tools()

    assert "Edit" not in allowed
    assert "Write" not in allowed
    assert "Bash" not in allowed
    assert "mcp__matmaster_eval__delegate_optimization" in allowed
```

- [ ] **Step 2: Run the single test to verify it fails**

Run: `uv run pytest tests/evaluation/test_devshell_agent_sdk_tools.py::test_main_agent_allowed_tools_exclude_edit_write_and_bash -v`

Expected: FAIL because helper does not exist or because write tools are still allowed.

- [ ] **Step 3: Write the failing test for optimization follow-up trigger handling**

```python
def test_optimization_followup_needed_only_when_queue_has_current_iteration(tmp_path: Path) -> None:
    state = _build_state(tmp_path)
    state.optimization_delegations_pending.append(
        {
            "iteration_index": 2,
            "optimization_round": 1,
            "problem_summary": "demo",
            "symptom": "demo",
            "suggested_focus": ["matmaster/skills"],
            "allowed_evidence_paths": ["matmaster/skills/demo/SKILL.md"],
            "notes": "demo",
        }
    )

    loop = DevshellAgentLoop(_build_config(tmp_path))

    assert loop._optimization_escalations_for_iteration(1, state) == []
    assert len(loop._optimization_escalations_for_iteration(2, state)) == 1
```

- [ ] **Step 4: Run the single test to verify it fails**

Run: `uv run pytest tests/evaluation/test_devshell_agent_sdk_tools.py::test_optimization_followup_needed_only_when_queue_has_current_iteration -v`

Expected: FAIL because helper does not exist.

- [ ] **Step 5: Implement minimal loop helpers and main-agent allowlist**

```python
class DevshellAgentLoop:
    @staticmethod
    def main_agent_allowed_tools() -> list[str]:
        return [
            *MatmasterEvalMcpToolkit.allowed_tool_names(),
            "Read",
            "Glob",
            "Grep",
        ]

    @staticmethod
    def _optimization_escalations_for_iteration(
        it: int, state: AgentLoopSharedState
    ) -> list[dict[str, Any]]:
        return [
            row
            for row in state.optimization_delegations_pending
            if int(row.get("iteration_index", -1)) == it
        ]
```

- [ ] **Step 6: Run the focused test file to verify it passes**

Run: `uv run pytest tests/evaluation/test_devshell_agent_sdk_tools.py -v`

Expected: PASS with the new loop-helper tests.

- [ ] **Step 7: Commit the loop helper change**

```bash
git add tests/evaluation/test_devshell_agent_sdk_tools.py evaluation/devshell_agent/loop.py
git commit -m "test: cover three-agent loop restrictions"
```

### Task 3: Implement Optimization Follow-Up Sessions In The Loop

**Files:**
- Modify: `evaluation/devshell_agent/loop.py`
- Modify: `evaluation/devshell_agent/sdk_tools.py`
- Modify: `evaluation/devshell_agent/config_state.py`

- [ ] **Step 1: Write the failing test for optimization report warnings**

```python
def test_run_optimization_followup_returns_warning_when_report_missing(tmp_path: Path) -> None:
    state = _build_state(tmp_path)
    state.optimization_delegations_pending.append(
        {
            "iteration_index": 1,
            "optimization_round": 1,
            "problem_summary": "demo",
            "symptom": "demo",
            "suggested_focus": ["matmaster/skills"],
            "allowed_evidence_paths": ["matmaster/skills/demo/SKILL.md"],
            "notes": "demo",
        }
    )
    loop = DevshellAgentLoop(_build_config(tmp_path))

    with patch("evaluation.devshell_agent.loop.ClaudeSDKClient", new=_FakeClaudeClient):
        rc = asyncio.run(
            loop._run_optimization_followups_if_needed(
                it=1,
                state=state,
                mcp_server={},
                loop_log=io.StringIO(),
            )
        )

    assert rc == 1
```

- [ ] **Step 2: Run the single test to verify it fails**

Run: `uv run pytest tests/evaluation/test_devshell_agent_sdk_tools.py::test_run_optimization_followup_returns_warning_when_report_missing -v`

Expected: FAIL because `_run_optimization_followups_if_needed` does not exist.

- [ ] **Step 3: Implement optimization follow-up session execution**

```python
SYSTEM_PROMPT_OPTIMIZATION = """You are the product-only optimization worker...
- You must not read or write any path under evaluation/.
- You may edit product-side files only.
- End each optimization sub-round by calling report_optimization_result.
"""
```

```python
async def _run_optimization_followups_if_needed(...) -> int:
    delegations = self._optimization_escalations_for_iteration(it, state)
    if not delegations:
        return 0

    report_count_before = len(state.optimization_reports)
    for delegation in delegations:
        async with ClaudeSDKClient(options=co) as cc:
            await cc.query(self._optimization_user_message(it=it, delegation=delegation))
            async for message in cc.receive_response():
                self._log_sdk_message(message, loop_log)

        reports = [
            row
            for row in state.optimization_reports[report_count_before:]
            if int(row.get("iteration_index", -1)) == it
            and int(row.get("optimization_round", -1)) == delegation["optimization_round"]
        ]
        if not reports:
            return 1

    state.optimization_delegations_pending = [
        row for row in state.optimization_delegations_pending if int(row.get("iteration_index", -1)) != it
    ]
    return 0
```

- [ ] **Step 4: Update main loop ordering to run optimization follow-ups before checklist follow-up**

```python
opt_rc = await self._run_optimization_followups_if_needed(...)
if opt_rc >= 1:
    exit_code = 1

follow_rc = await self._run_checklist_followup_if_needed(...)
```

- [ ] **Step 5: Run the focused test file to verify it passes**

Run: `uv run pytest tests/evaluation/test_devshell_agent_sdk_tools.py -v`

Expected: PASS with optimization follow-up coverage and no regressions in checklist behavior.

- [ ] **Step 6: Commit the optimization follow-up implementation**

```bash
git add evaluation/devshell_agent/loop.py evaluation/devshell_agent/sdk_tools.py evaluation/devshell_agent/config_state.py tests/evaluation/test_devshell_agent_sdk_tools.py
git commit -m "feat: add optimization follow-up agent sessions"
```

### Task 4: Add Persistent History Outside Results

**Files:**
- Modify: `evaluation/devshell_agent/loop.py`
- Modify: `evaluation/devshell_agent/config_state.py`
- Modify: `evaluation/scripts/devshell/run_devshell_agent_loop.py`

- [ ] **Step 1: Write the failing test for persistent history directory selection**

```python
def test_default_history_dir_is_outside_results(tmp_path: Path) -> None:
    cfg = _build_config(tmp_path)
    loop = DevshellAgentLoop(cfg)

    history_dir = loop._history_root()

    assert history_dir == tmp_path / "evaluation" / "devshell_agent_history"
    assert "results" not in str(history_dir)
```

- [ ] **Step 2: Run the single test to verify it fails**

Run: `uv run pytest tests/evaluation/test_devshell_agent_sdk_tools.py::test_default_history_dir_is_outside_results -v`

Expected: FAIL because `_history_root` does not exist.

- [ ] **Step 3: Implement history-root config and summary writers**

```python
@dataclass
class AgentLoopConfig:
    # existing fields ...
    history_root: Path | None = None
```

```python
def _history_root(self) -> Path:
    if self._cfg.history_root is not None:
        return self._cfg.history_root
    return self._cfg.repo_root / "evaluation" / "devshell_agent_history"
```

```python
def _write_iteration_history(self, *, it: int, state: AgentLoopSharedState, outcome: dict[str, Any]) -> None:
    session_history = self._history_root() / self._cfg.session_dir.name / "iterations"
    session_history.mkdir(parents=True, exist_ok=True)
    payload = {
        "iteration_index": it,
        "outcome": outcome,
        "optimization_reports": [r for r in state.optimization_reports if int(r.get("iteration_index", -1)) == it],
        "checklist_reports": [r for r in state.checklist_revision_reports if int(r.get("iteration_index", -1)) == it],
    }
    (session_history / f"iter_{it:02d}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
```

- [ ] **Step 4: Wire CLI config to populate the history root**

```python
cfg = AgentLoopConfig(
    # existing args ...
    history_root=(repo_root / "evaluation" / "devshell_agent_history").resolve(),
)
```

- [ ] **Step 5: Run the focused test file to verify it passes**

Run: `uv run pytest tests/evaluation/test_devshell_agent_sdk_tools.py -v`

Expected: PASS with history-path coverage.

- [ ] **Step 6: Commit the persistent-history implementation**

```bash
git add evaluation/devshell_agent/loop.py evaluation/devshell_agent/config_state.py evaluation/scripts/devshell/run_devshell_agent_loop.py tests/evaluation/test_devshell_agent_sdk_tools.py
git commit -m "feat: persist devshell loop history outside results"
```

### Task 5: Update Runtime Prompting, Session Manifest, And Evaluation Docs

**Files:**
- Modify: `evaluation/devshell_agent/loop.py`
- Modify: `evaluation/devshell_agent/sdk_tools.py`
- Modify: `evaluation/AGENTS_evaluation.md`
- Modify: `docs/superpowers/specs/2026-04-08-devshell-three-agent-loop-design.md`

- [ ] **Step 1: Add prompt and manifest coverage for three-agent mode**

```python
payload = {
    # existing fields ...
    "history_root": str(self._history_root().resolve()),
    "enable_checklist_agent": cfg.enable_checklist_agent,
    "enable_optimization_agent": True,
}
```

```python
SYSTEM_PROMPT_MAIN = """... drive-only ...
- You must not edit files.
- You must not read any path under evaluation/.
- Use delegate_optimization for product-side work.
"""
```

- [ ] **Step 2: Update evaluation module documentation to describe the three-agent model**

```markdown
- **三 Agent**：主 Agent 只做 Drive，总结评分并显式委派；Checklist Agent 仅处理 `evaluation/**`；优化 Agent 严禁读取 `evaluation/**`，仅处理产品侧代码与提示。
- 主 Agent 与优化 Agent 都只接收脱敏摘要，不接收原始 `score_reason`。
- 持久历史写入 `evaluation/devshell_agent_history/`，不受 `--clean-results` 影响。
```

- [ ] **Step 3: Run the targeted test file and a smoke import**

Run: `uv run pytest tests/evaluation/test_devshell_agent_sdk_tools.py -v`

Expected: PASS

Run: `uv run python -c "from evaluation.devshell_agent.loop import DevshellAgentLoop; print('OK')"`

Expected: `OK`

- [ ] **Step 4: Commit the prompt and documentation updates**

```bash
git add evaluation/devshell_agent/loop.py evaluation/devshell_agent/sdk_tools.py evaluation/AGENTS_evaluation.md docs/superpowers/specs/2026-04-08-devshell-three-agent-loop-design.md
git commit -m "docs: describe three-agent devshell evaluation loop"
```

### Task 6: Full Verification And Review

**Files:**
- Modify: `tests/evaluation/test_devshell_agent_sdk_tools.py`
- Modify: `evaluation/devshell_agent/config_state.py`
- Modify: `evaluation/devshell_agent/sdk_tools.py`
- Modify: `evaluation/devshell_agent/loop.py`
- Modify: `evaluation/scripts/devshell/run_devshell_agent_loop.py`
- Modify: `evaluation/AGENTS_evaluation.md`

- [ ] **Step 1: Run the full targeted verification suite**

Run: `uv run pytest tests/evaluation/test_devshell_agent_sdk_tools.py tests/evaluation/test_devshell_agent_subprocess.py -v`

Expected: PASS with 0 failures.

- [ ] **Step 2: Run a repo-local syntax smoke check**

Run: `uv run python -m compileall evaluation/devshell_agent evaluation/scripts/devshell`

Expected: exit 0 and no compile errors.

- [ ] **Step 3: Review requirements against the spec**

```text
Check:
- Main agent cannot edit files and cannot read evaluation paths
- Checklist agent remains evaluation-only
- Optimization agent cannot access evaluation paths
- Multiple optimization delegations per iteration work
- Checklist id drift still stops the loop
- Persistent summaries live outside results
```

- [ ] **Step 4: Commit any final test-driven cleanup**

```bash
git add tests/evaluation/test_devshell_agent_sdk_tools.py evaluation/devshell_agent/config_state.py evaluation/devshell_agent/sdk_tools.py evaluation/devshell_agent/loop.py evaluation/scripts/devshell/run_devshell_agent_loop.py evaluation/AGENTS_evaluation.md
git commit -m "test: verify three-agent devshell loop"
```
