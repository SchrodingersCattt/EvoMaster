---
name: evaluation-iteration
description: Query evaluation run results from tools-server API, analyze failures, and iterate on question bank criteria. Use when reviewing eval runs, debugging failed checklist items, or refining question bank YAML.
---

# Evaluation Iteration Skill

This skill covers querying evaluation results from the online API, analyzing why runs fail, and iterating on question bank criteria.

## API Access

**Base URL:** `MATMASTER_TOOLS_SERVER` (resolved from `utils/env.py`; test = `https://matmaster-tools-server.test.bohrium.com`)

**Auth:** `Authorization: Bearer {MATMASTER_TOOLS_EVALUATION_BEARER}` (from `.env.test`)

### Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/evaluation/questions/score-summary` | GET | All questions baseline pass/fail overview |
| `/api/v1/evaluation/questions/{question_id}/overview` | GET | Single question: baseline + iteration runs history |
| `/api/v1/evaluation/ingest` | POST | Submit evaluation results |
| `/api/v1/evaluation/question-catalog/sync` | POST | Sync question bank metadata |

### Query a Specific Run

```bash
curl -s -H "Authorization: Bearer $MATMASTER_TOOLS_EVALUATION_BEARER" \
  "$MATMASTER_TOOLS_SERVER/api/v1/evaluation/questions/{question_id}/overview" \
  | python3 -m json.tool
```

Response structure:
```json
{
  "code": 0,
  "data": {
    "question_id": "...",
    "question_text": "...",
    "claude_code": { "run_id": "...", "run_kind": "baseline", ... },
    "cursor": { ... },
    "codex": { ... },
    "iterations": [
      {
        "run_id": "...",
        "all_repeats_passed": false,
        "repeats": [
          {
            "run_id": "...",
            "passed": false,
            "score_reason": "### Correctness\n- **`item_id`** (`verifier`): ✓ pass / ✗ fail — reason\n...\n**Overall weighted score:** 0.844",
            "num_turns": 19,
            "tokens": 26470,
            "duration_ms": 113244,
            "artifact_id": "...",
            "extra": { "mode": "...", "usage": {...}, "eval_tooling": {...} }
          }
        ]
      }
    ]
  }
}
```

### Extract Failed Criteria from a Run

```python
import json, sys

data = json.load(sys.stdin)['data']
runs = data['iterations']
target = [r for r in runs if r['run_id'] == TARGET_RUN_ID][0]
repeat = target['repeats'][0]

# Parse score_reason for failures
for line in repeat['score_reason'].split('\n'):
    if '✗ fail' in line:
        print(line)
```

### Retrieve Agent Trajectory (Events Log)

Each eval repeat stores an artifact bundle with logs and workspace files. Use the artifact API to inspect what the agent actually did:

```bash
# 1. Get artifact_id from the overview response (per repeat)
ARTIFACT_ID="<from overview response>.repeats[N].artifact_id"

# 2. List files in the artifact
curl -s -H "Authorization: Bearer $MATMASTER_TOOLS_EVALUATION_BEARER" \
  "$MATMASTER_TOOLS_SERVER/api/v1/artifacts/$ARTIFACT_ID/files?path=logs" \
  | python3 -m json.tool

# 3. Navigate into the task log directory (named like {question_id}_{mode}_r{N})
curl -s -H "Authorization: Bearer $MATMASTER_TOOLS_EVALUATION_BEARER" \
  "$MATMASTER_TOOLS_SERVER/api/v1/artifacts/$ARTIFACT_ID/files?path=logs/{task_id}" \
  | python3 -m json.tool
# Returns: devshell_console.log + events_*.jsonl

# 4. Download the events JSONL (the full agent trajectory)
curl -s -H "Authorization: Bearer $MATMASTER_TOOLS_EVALUATION_BEARER" \
  "$MATMASTER_TOOLS_SERVER/api/v1/artifacts/$ARTIFACT_ID/content?path=logs/{task_id}/events_*.jsonl"
```

Events JSONL structure (one JSON object per line):
- `type: "tool_call"` — agent invoked a tool (`tool`, `args`, `call_id`)
- `type: "tool_result"` — tool returned (`content`, `call_id`)
- `type: "response"` — assistant text output (`content`)
- `type: "run_result"` — final status (`status`, `reason`)

```bash
# 5. Get workspace files the agent produced
curl -s -H "Authorization: Bearer $MATMASTER_TOOLS_EVALUATION_BEARER" \
  "$MATMASTER_TOOLS_SERVER/api/v1/artifacts/$ARTIFACT_ID/files?path=workspaces/{task_id}"

# 6. Read a specific output file
curl -s -H "Authorization: Bearer $MATMASTER_TOOLS_EVALUATION_BEARER" \
  "$MATMASTER_TOOLS_SERVER/api/v1/artifacts/$ARTIFACT_ID/content?path=workspaces/{task_id}/relax/OUT/INPUT"
```

## Failure Analysis Workflow

1. **Get the run result** via the overview API
2. **Identify failed criteria** — look at `score_reason` for `✗ fail` lines
3. **Categorize the failure**:
   - **Agent capability issue**: agent genuinely can't do the task → consider if prompt needs more guidance
   - **Overly strict criteria**: agent produced physically valid output but failed regex/token checks → relax criteria
   - **Criteria bug**: verification logic is wrong (e.g., checking `calculation scf` for a relax task) → fix criteria
   - **Efficiency overshoot**: task completed correctly but exceeded turn/token budget → adjust budget
4. **Determine the fix type** (see below)

## Iteration Decision Matrix

| Failure Type | Action | Risk |
|-------------|--------|------|
| Criteria has a bug (wrong regex, wrong expected value) | Fix the criterion | None — this is a bug fix |
| Agent produces valid alternative not covered by regex | Widen regex or use `llm_binary_judge` | Low — accepting valid alternatives |
| Agent lacks domain knowledge (e.g., AFM for NiO) | Add explicit instruction in prompt | Medium — reduces difficulty |
| Agent uses too many turns for structural work | Provide input files via `data_files` | Medium — changes what's tested |
| Token/turn budget too tight for legitimate approach | Increase budget (verify with data) | Low if data-driven |
| Remove a criterion entirely | Delete from checklist | High — losing coverage |

## Question Bank YAML Modification

Question files live in `evaluation/question_bank/<capability>/<xx>_<domain>.yaml`.

For field mapping rules (capability, domain, tags, ID prefix, file location, skill correspondence) → `references/question_taxonomy.md`

### Key Fields

```yaml
- id: IG_abacus_017_20260512       # Convention: {capability}_{domain}_{seq}_{date}
  capability: input_generation
  domain: agnostic
  intent: Short description of what the question tests
  human_prompt_seed: |
    The actual prompt given to the agent...
  tags: [eng_abacus, struct_transform]
  data_files:                        # Optional: files provided to agent workspace
  - key: rocksalt_poscar
    path: data/IG_abacus_017_20260512/POSCAR_rocksalt
    oss_url: ''
    description: POSCAR for the rocksalt NiO candidate structure.
  reference_answers:                 # Verification criteria
  - key: must_input_core
    value:
      filename: nio_rocksalt/INPUT
      tokens: [calculation cell-relax, basis_type lcao, cal_force 1]
      flags: i
  - key: must_numerics
    value:
      filename: nio_rocksalt/INPUT
      checks:
      - key: ecutwfc
        min: 80.0
        max: 150.0
  scoring_checklist:
  - id: must_input_core
    criterion: '[Must] INPUT contains core required parameters.'
    axis: correctness
    verify: text_file_contains_all
    weight: 1.0
  - id: variable_phase_structures
    criterion: '[Variable] Structures represent distinct polymorphs.'
    axis: correctness
    verify: llm_binary_judge
    weight: 0.8
  - id: turn_budget
    criterion: Agent completes within turn budget.
    axis: efficiency
    verify: turn_budget
```

### Verifier Types

| Verifier | What it checks |
|----------|---------------|
| `text_file_contains_all` | All tokens present in file (case-insensitive with `flags: i`) |
| `text_file_regex` | File content matches regex pattern |
| `text_file_numeric_range` | Numeric values within [min, max] range (line-based key-value or JSON) |
| `json_file_numeric_range` | JSON file value at dot-path key within expected ± tolerance |
| `json_file_schema` | JSON file is valid and contains required top-level keys |
| `json_file_artifacts` | Files referenced inside a JSON array exist in workspace |
| `artifact_exists` | Output file exists in workspace |
| `token_budget` | Total tokens ≤ max |
| `turn_budget` | Total steps ≤ max |
| `duration_budget` | Duration ≤ max ms |
| `no_retries` | No retry tool calls detected |
| `llm_binary_judge` | LLM judges criterion pass/fail (use for semantic checks) |

### ID Date Convention

When modifying a question's criteria or prompt, bump the date suffix in the ID:
```
IG_abacus_017_20260511 → IG_abacus_017_20260512
```

## Best Practices

- **Don't lower difficulty without justification** — if agent fails, first check if the criterion itself is buggy
- **Prefer deterministic verifiers** over `llm_binary_judge` when possible (regex, token checks, numeric range)
- **Provide input files** (`data_files`) instead of expecting agent to construct complex structures from scratch — tests format conversion, not crystallography knowledge
- **Token/turn budgets should be data-driven** — check actual successful runs before setting limits
- **One change at a time** — don't simultaneously relax criteria AND change the prompt; isolate variables
- **Keep prompt changes minimal** — adding "please do X" is fine; removing core difficulty is a design decision that needs justification

## Frontend Evaluation Page

The evaluation dashboard is at:
```
https://matmaster.{env}.bohrium.com/matmaster/evaluation?qid={question_id}&run_id={run_id}&ov_ch=iteration
```

Parameters:
- `qid`: question ID (e.g., `IG_abacus_017_20260511`)
- `run_id`: specific run UUID
- `ov_ch`: channel (`iteration`, `claude_code`, `cursor`, `codex`)
