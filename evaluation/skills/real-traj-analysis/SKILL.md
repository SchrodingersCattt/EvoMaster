---
name: real-traj-analysis
description: Analyze real user session trajectories from admin API to identify pain points, extract evaluation question candidates, and track analysis progress. Use when reviewing session quality, finding coverage gaps from real usage, or generating new eval questions from observed failures.
---

# Real Trajectory Analysis

Analyze real user sessions to find where the agent is not smooth, identify recurring failure patterns, and generate evaluation questions that drive iteration.

## API Access

Auth and endpoints → `references/api.md`

## Workflow

### 1. Select Sessions

Pull sessions with `min_events >= 10`, ordered by `created_at asc`. Skip already-analyzed sessions (check existing `traj_analysis/{env}/` files).

**Important rules:**
- Do NOT skip sessions before analysis — verdict is an *output*, not a pre-filter
- Analyze in strict chronological order from where the last analysis left off
- Pull ALL events (paginate with `after_event_id` if >200) — never judge from partial data

### 2. Analyze Session

For each session, read through intermediate tool call arguments and results. Assess:

| Dimension | What to look for |
|-----------|-----------------|
| Completion | Did the task finish? Error/cancelled at end? |
| Smoothness | Retries, backtracking, unnecessary detours, excessive deliberation |
| Recovery | On tool/infra failure: what failed, what agent did next, was user informed |

**Actionable threshold: "first-try correct".** Any self-correction indicates a gap worth testing:
- Wrong parameters → self-corrected: **actionable**
- Wrong tool/approach → pivoted: **actionable**
- Wrong result → noticed and fixed: **actionable**

**Job submission failures deserve special scrutiny.** When a Bohrium/computation job fails, classify root cause:

| Root cause | Actionable? | Example |
|-----------|-------------|---------|
| Agent's wrong parameters in script/input | **YES** | mixing_beta too high → SCF diverge; wrong PP filename; IDPP at interface |
| Agent's wrong image/command/path | Maybe | Image typo (environment-specific, hard to test stably) |
| Agent didn't validate before submit | **YES** | Skipped `ls pp_library/` → wrong filenames; skipped geometry check → OOM |
| Pure infra (storePath, quota, network) | No | Agent had no way to avoid |

The key question: **could the agent have avoided this failure with domain knowledge or a simple check?** If yes → actionable.

Only exclude from eval consideration:
- Pure infrastructure failures where agent had no choice (storePath, auth, network)
- Tool API limitations agent cannot work around
- Runtime-dependent issues that cannot be stably reproduced (e.g., image name changes, version-specific API behavior)

**Skill docs do not guarantee compliance.** Every critical decision point in skills should have a corresponding eval question.

### 3. Record Analysis

Write to `evaluation/traj_analysis/{env}/{YYYY-MM}.json`:

```json
{
  "version": 2,
  "environment": "test|prod",
  "month": "2026-05",
  "sessions": {
    "session_id_here": {
      "session_id": "...",
      "max_event_id": 134,
      "analyzed_at": "2026-05-13",
      "event_count": 134,
      "user_id": "...",
      "created_at_ms": 1776615526000,
      "verdict": "skip|informational|actionable",
      "summary": "One paragraph description of what happened.",
      "issues": ["issue 1", "issue 2"],
      "eval_recommendation": "What question to add, or 'none'.",
      "software_tags": ["ABACUS", "DPA/MLIPs"],
      "task_type": "计算任务|文献调研|结构建模|Q&A调试|学术写作|数据提取|其他"
    }
  }
}
```

**Verdict meanings:**
- `skip`: No agent behavior to analyze (infra failure before agent action, off-topic, empty session)
- `informational`: Agent executed but no new eval question needed (already covered, or untestable infra/platform issue)
- `actionable`: Should generate a new evaluation question

**`software_tags`** — standard vocabulary:
`ABACUS`, `VASP`, `LAMMPS`, `CP2K`, `ORCA`, `GROMACS`, `GPUMD`, `DPA/MLIPs`, `DP-GEN`, `XRD`, `TEM/电镜`, `DSC`, `pymatgen`, `ASE`, `mat_sg`, `PaperSearch`, `WebSearch`

**`task_type`** — single value:
`计算任务`, `文献调研`, `结构建模`, `Q&A/调试`, `学术写作`, `数据提取`, `其他`

### 4. Generate Questions

For `actionable` sessions, design eval questions. **Do not 透题** — reconstruct the real user task; keep failure modes, quality gates, source requirements, and answer-shaped fixture fields out of the prompt, and put evaluation intent in the hidden checklist/judge. Rules + self-check → `references/question_design.md`

### 5. Settle Release Gate

When actionable sessions suggest strong regression cases → `references/release_gate.md`

### 6. Distribution Summary

After each analysis batch, report:
1. **软件/工具分布**: `software_tags` frequency, descending
2. **任务类型分布**: `task_type` percentages
3. **Verdict 分布**: skip / informational / actionable counts

Used to detect coverage gaps and track usage trends.

### 7. Review Cycle

After writing questions:
1. Run the realistic-design self-check (透题 / answer-shaped fixtures) → `references/question_design.md`
2. Check scope correctness: Does the checklist reference platform-specific values? → platform scope
3. Check for redundancy: Is there already a question testing the same rule/capability?
4. Validate YAML: `python -c "import yaml; yaml.safe_load(open(f))"`
5. Update `manifest.yaml` question counts

## File Structure

```
evaluation/
├── traj_analysis/           (gitignored — local analysis state)
│   ├── test/
│   └── prod/
├── release_gate/
│   └── cases.yaml
├── question_bank/
└── skills/
    └── real-traj-analysis/
        ├── SKILL.md          (this file)
        └── references/
            ├── api.md
            ├── question_design.md
            └── release_gate.md
```
