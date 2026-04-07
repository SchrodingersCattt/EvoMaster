# DevShell Three-Agent Loop Design

## Context

The current DevShell self-iteration loop already separates the main iteration agent from a follow-up checklist agent, but the main agent can still read `evaluation/**` and then optimize product-side code with direct knowledge of question-bank and evaluator details. This creates a strong overfitting risk and weakens the fairness boundary between product optimization and rubric maintenance.

This design changes the loop into a strict three-agent architecture:

- `Main Agent`: drive-only, no file edits, no `evaluation/**` access
- `Checklist Agent`: evaluator and question-bank maintenance only
- `Optimization Agent`: product-side optimization only, no `evaluation/**` access

The existing exit rule remains unchanged: if the checklist follow-up changes question ids, the outer loop must stop immediately.

## Goals

- Eliminate the current "main agent reads evaluation internals and optimizes against them" cheating risk.
- Make agent responsibilities auditable, explicit, and enforced by the orchestrator rather than prompt text alone.
- Allow multiple optimization sub-rounds inside one outer iteration.
- Preserve the existing checklist-id drift stop rule.
- Persist iteration summaries outside `results/` so the loop can avoid repeating failed ideas even when `results/` is cleaned.

## Non-Goals

- Replacing the existing `run_devshell_eval` execution model.
- Changing the scoring pipeline semantics.
- Removing the checklist follow-up mechanism.
- Solving general agent memory beyond the DevShell loop history layer.

## Current Problems

### Leakage Risk

The current main agent can read `evaluation/question_bank/**`, `evaluation/core/**`, and evaluation outputs, then directly modify `matmaster/**` and related product-side files. Even if it does not edit the question bank, it can still optimize behavior against rubric specifics.

### Weak Role Boundaries

The main agent currently mixes three responsibilities:

- Drive the loop
- Decide whether checklist maintenance is needed
- Perform product-side code edits

This makes review and attribution harder.

### Ephemeral Loop Memory

The CLI currently clears `results/` by default. Useful iteration knowledge can disappear between runs, which increases retry churn and makes it easier for the loop to revisit already-failed changes.

## Proposed Architecture

### Agent Roles

#### Main Agent

The main agent becomes a strict orchestrator-facing drive agent.

Responsibilities:

- start each outer iteration
- call `run_devshell_eval`
- read sanitized scoring summaries produced by the orchestrator
- decide whether to delegate checklist work
- decide whether to delegate one or more optimization sub-rounds
- summarize the iteration and call `report_iteration_outcome`

Hard restrictions:

- cannot read `evaluation/**`
- cannot edit any code or file
- cannot receive raw rubric text or raw `score_reason`

#### Checklist Agent

The checklist agent remains the only agent allowed to modify evaluation-side artifacts.

Responsibilities:

- inspect evaluator and question-bank evidence after explicit delegation
- modify `evaluation/question_bank/**` when rubric or reference data is wrong
- modify `evaluation/core/**` when evaluator logic itself is wrong
- call `report_checklist_revision` exactly once per checklist round

Hard restrictions:

- cannot edit product-side directories
- is only started after explicit main-agent delegation
- remains subject to the question-id drift stop rule

#### Optimization Agent

The optimization agent is a new product-side worker role.

Responsibilities:

- inspect sanitized problem summaries
- inspect non-`evaluation/**` evidence paths
- modify product-side code and prompts
- make one or more commits inside a delegated optimization sub-round
- call `report_optimization_result` exactly once per optimization sub-round

Hard restrictions:

- cannot read or write any path under `evaluation/**`
- cannot receive raw rubric wording, raw `score_reason`, or question-bank excerpts

### Separation Model

This design enforces isolation in three layers:

1. Prompt-level role definitions
2. Tool allowlists per agent
3. Path and payload restrictions enforced by the orchestrator

Prompt instructions alone are not considered sufficient.

## Delegation Model

### Explicit Checklist Delegation

The existing `escalate_checklist_revision` tool remains the only way for the main agent to request checklist-side work.

The main agent provides a structured delegation payload, not direct file edits.

Recommended payload:

- `iteration_index`
- `question_ids`
- `problem_summary`
- `rationale`
- `evidence_paths`

### Explicit Optimization Delegation

Add a new `delegate_optimization` tool. This is the only way for the main agent to start an optimization sub-round.

Recommended payload:

- `iteration_index`
- `problem_summary`
- `symptom`
- `suggested_focus`
- `allowed_evidence_paths`
- `notes`

Payload rules:

- `allowed_evidence_paths` must not contain anything under `evaluation/**`
- `notes` must not contain raw rubric wording, reference-answer details, or raw `score_reason`

### Optimization Reporting

Add a new `report_optimization_result` tool. Every optimization sub-round must end with exactly one report.

Recommended payload:

- `iteration_index`
- `optimization_round`
- `summary`
- `files_touched`
- `commit_shas`
- `needs_more_work`
- `followup_suggestion`

## Iteration Flow

Each outer iteration follows this order:

1. Main agent starts the round.
2. Main agent calls `run_devshell_eval`.
3. The orchestrator returns a sanitized scoring summary to the main agent.
4. Main agent decides whether checklist work is needed.
5. Main agent decides whether optimization work is needed.
6. Main agent may call `delegate_optimization` multiple times in the same iteration.
7. Each optimization delegation starts one independent optimization sub-round.
8. Each optimization sub-round ends with `report_optimization_result`.
9. After all optimization sub-rounds finish, the main agent continues its drive logic.
10. If checklist work was delegated, the orchestrator starts the checklist agent after the main-agent turn ends.
11. Checklist agent ends with `report_checklist_revision`.
12. The orchestrator compares question-bank id sets before and after checklist follow-up.
13. If ids changed, the orchestrator writes drift metadata and stops the outer loop.
14. If ids did not change, the loop can continue to the next outer iteration.

This preserves the existing checklist follow-up timing while adding optimization sub-rounds as explicit worker sessions.

## Scoring Data Exposure

### Main Agent View

The main agent receives a sanitized score summary rather than raw evaluator-facing detail.

The summary should include enough information to drive decisions, for example:

- current macro score
- low-score task ids
- problem categories
- high-level failure symptoms
- safe evidence paths outside `evaluation/**`

The summary must exclude:

- raw rubric text
- raw `score_reason`
- reference-answer wording
- direct excerpts from question-bank checklist entries

### Optimization Agent View

The optimization agent only receives main-agent-generated sanitized summaries plus orchestrator-validated safe evidence paths.

It must never see:

- any file content from `evaluation/**`
- raw `score_reason`
- direct question-bank or evaluator semantics

### Checklist Agent View

The checklist agent may inspect evaluation-side evidence because its role is evaluation maintenance. This access is limited to delegated checklist rounds only.

## Tool and Permission Model

### Main Agent Tools

Allowed:

- `run_devshell_eval`
- `report_iteration_outcome`
- `escalate_checklist_revision`
- `delegate_optimization`
- read-only helper tools that are path-filtered away from `evaluation/**`

Disallowed:

- `Edit`
- `Write`
- unrestricted `Bash`

The preferred implementation is to remove write tools entirely rather than rely on prompt instructions.

### Checklist Agent Tools

Allowed:

- `report_checklist_revision`
- `Read`
- `Glob`
- `Grep`
- `Edit`
- `Write`
- optional `Bash` if path-restricted

Write scope:

- `evaluation/question_bank/**`
- `evaluation/core/**`

Everything else is denied for writes.

### Optimization Agent Tools

Allowed:

- `report_optimization_result`
- `Read`
- `Glob`
- `Grep`
- `Edit`
- `Write`
- optional `Bash` if path-restricted

Read and write scope:

- allowed product-side paths such as `matmaster/**`, `config/**`, and selected `src/**`

Denied:

- any path under `evaluation/**`

## Shared State and History

### Session Runtime State

Extend shared orchestrator state to track:

- checklist delegations pending
- checklist reports
- optimization delegations pending or completed
- optimization reports per iteration
- optimization round counters per iteration

### Persistent History

Because `results/` may be cleaned at the start of each run, add a persistent history root outside `results/`.

Recommended location:

- `evaluation/devshell_agent_history/`

Recommended structure:

- `evaluation/devshell_agent_history/index.jsonl`
- `evaluation/devshell_agent_history/<session_id>/session_summary.json`
- `evaluation/devshell_agent_history/<session_id>/iterations/iter_01.json`
- `evaluation/devshell_agent_history/<session_id>/iterations/iter_02.json`

Purpose:

- keep cross-run memory of attempted directions
- help the main agent avoid repeating failed ideas
- preserve auditability even when transient run artifacts are removed

### Iteration Summary Content

Each persisted iteration summary should record at least:

- iteration index
- macro score before and after
- main-agent summary
- optimization delegations
- optimization reports
- checklist delegation summary
- checklist revision report
- touched files
- commit shas
- stop or continue rationale
- next-iteration hypotheses
- avoid-next-time notes
- question-bank id drift status

## Exit Logic

The existing checklist-id drift guard remains unchanged in meaning.

Required behavior:

- snapshot question ids before checklist follow-up
- snapshot again after checklist follow-up
- if the id set changes, write `question_bank_id_drift.json`
- stop the outer loop immediately

Optimization sub-rounds do not participate in this guard because they cannot access `evaluation/**`.

## Failure Handling

### Optimization Failure

If an optimization sub-round fails:

- record a structured optimization failure report
- return control to the main agent
- let the main agent decide whether to open another optimization sub-round or stop

This should not automatically abort the whole outer iteration.

### Checklist Failure

If the checklist agent fails to report:

- keep the current warning behavior
- mark the loop run as having warnings

### Evaluation Failure

If `run_devshell_eval` fails:

- the main agent may summarize the failure
- the main agent must not edit code directly
- the iteration may stop early with a truthful outcome report

## Implementation Notes

### Minimal-Change Strategy

To reduce risk, reuse the existing checklist follow-up architecture and extend it symmetrically for optimization sub-rounds.

This implies:

- keep `run_devshell_eval`
- keep `report_iteration_outcome`
- keep `escalate_checklist_revision`
- keep `report_checklist_revision`
- add `delegate_optimization`
- add `report_optimization_result`
- add optimization follow-up session execution in the loop orchestrator

### Summary Sanitization

Sanitization should happen inside orchestrator-owned tool code, not inside the main agent prompt. The main agent should never be trusted to self-sanitize evaluation detail.

### Results Cleaning Compatibility

The new persistent history root must not live under `results/`, otherwise `--clean-results` defeats the anti-repetition goal.

## Testing Plan

At minimum, add tests for:

### Role Isolation

- main agent cannot write files
- main agent cannot read `evaluation/**`
- optimization agent cannot read or write `evaluation/**`
- checklist agent cannot write product-side paths

### Delegation Flow

- checklist agent only starts after explicit `escalate_checklist_revision`
- optimization sub-round only starts after explicit `delegate_optimization`
- one outer iteration can trigger multiple optimization sub-rounds
- every optimization sub-round requires `report_optimization_result`

### Exit Guard

- checklist id change stops the loop
- non-id checklist changes do not stop the loop
- optimization changes never trigger checklist-id drift logic

### Persistent History

- history files remain after `--clean-results`
- iteration summaries are written after each round
- session index is appended after each loop session

## Open Decisions Resolved

This design fixes the following decisions:

- Use explicit tool delegation for checklist work: yes
- Use explicit tool delegation for optimization work: yes
- Allow multiple optimization delegations in one outer iteration: yes
- Keep checklist-id drift as the stop signal: yes
- Hide raw `score_reason` from both main and optimization agents: yes
- Persist summaries outside `results/`: yes

## Recommended Next Step

Write an implementation plan that updates:

- `evaluation/devshell_agent/loop.py`
- `evaluation/devshell_agent/sdk_tools.py`
- `evaluation/devshell_agent/config_state.py`
- `evaluation/scripts/devshell/run_devshell_agent_loop.py`
- `tests/evaluation/test_devshell_agent_sdk_tools.py`
- loop-related documentation under `evaluation/AGENTS_evaluation.md`

