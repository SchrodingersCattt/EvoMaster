---
name: traj-analysis
description: Analyze real user session trajectories from admin API to identify pain points, extract evaluation question candidates, and track analysis progress. Use when reviewing session quality, finding coverage gaps from real usage, or generating new eval questions from observed failures.
---

# Trajectory Analysis Skill

Analyze real user sessions to find where the agent is not smooth, identify recurring failure patterns, and generate evaluation questions that drive iteration.

## API Access

**Endpoints:**

| Environment | Base URL |
|-------------|----------|
| test | `https://matmaster-evo.test.bohrium.com` |
| prod | `https://matmaster-evo.bohrium.com` |

**Auth:** `X-User-Id` header. Must be in tools-server `allowlist.admin`.

User ID 从对应环境的 `.env.*` 文件中获取 `BOHRIUM_USER_ID` 字段：
- test: `.env.test`
- prod: `.env.prod`

### List Sessions

```
GET /api/v1/admin/chat/sessions
  ?sort_by=created_at|last_event_at|event_count
  &order=asc|desc
  &since=ISO8601
  &until=ISO8601
  &min_events=N
  &user_id=xxx (optional filter)
  &limit=N&offset=N
```

### Get Session Events

```
GET /api/v1/admin/chat/sessions/{session_id}/events
  ?after_event_id=N (incremental pull)
  &include_spawn=true|false
  &limit=N
```

Response includes `max_event_id` for tracking analysis state.

## Workflow

### 1. Select Sessions

```bash
curl -s "$BASE/api/v1/admin/chat/sessions?sort_by=created_at&order=asc&min_events=10&limit=10" \
  -H "X-User-Id: $ADMIN_UID"
```

Filter criteria:
- `min_events >= 10` to skip empty/abandoned sessions
- Skip sessions already in local analysis records
- Prioritize: non-developer users, recent dates, high event counts

### 2. Analyze Session

For each session, pull full events and assess:

1. **Was the task completed?** (look for finish/response at end vs error/cancelled)
2. **Was it smooth?** Check for:
   - Repeated identical tool calls (retry loops)
   - Excessive think/deliberation steps (>5 consecutive)
   - Tool call failures followed by chaotic fallbacks
   - finish called multiple times
   - Unrelated output (agent went off-rails)
3. **What went wrong?** Categorize issues:
   - `knowledge_gap`: Agent lacked domain knowledge
   - `tool_failure`: MCP tool failed, agent couldn't recover gracefully
   - `prompt_misunderstanding`: Agent misinterpreted user intent
   - `efficiency`: Completed but with excessive steps/retries
   - `platform_config`: Wrong paths, images, or submission parameters

### 3. Record Analysis

Write to `evaluation/traj_analysis/{env}/{YYYY-MM}.json`:

```json
{
  "version": 1,
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
      "eval_recommendation": "What question to add, or 'none'."
    }
  }
}
```

**Verdict meanings:**
- `skip`: Not useful (dev test, repeated prompt, old architecture)
- `informational`: Interesting pattern but no new question needed (already covered)
- `actionable`: Should generate a new evaluation question

### 4. Generate Questions

For `actionable` sessions, design questions following these principles:

**Prompt rules:**
- Write as a natural user would ask — no internal terminology
- Never mention tool names, file formats, or method names that would hint at the solution
- Never mention platform names (Bohrium) unless testing general user intent
- Don't specify output filenames unless the user naturally would

**Scope classification:**
- `knowledge`: Tests domain knowledge + correct execution (no platform specifics in checklist)
- `platform`: Tests Bohrium runtime integration (paths, images, manifest, submission)

**Checklist rules:**
- Prefer deterministic verifiers (`text_file_regex`, `struct_file_*`, `artifact_exists`)
- Avoid `llm_binary_judge` unless no deterministic alternative exists
- Don't duplicate checks already covered by existing questions
- Use `verify_params` with glob patterns when filenames are not user-specified

**Deduplication:**
- Before adding a question, check if the same *capability point* is already tested
- Different scenarios of the same capability (e.g., TBG vs CNT) are valid if they test different physical knowledge
- Same scenario with different parameters (e.g., Si SCF vs Ge SCF) is NOT worth duplicating

### 5. Review Cycle

After writing questions:
1. Check for prompt hints (透题): Does the prompt give away any part of the solution?
2. Check scope correctness: Does the checklist reference platform-specific values? → platform scope
3. Check for redundancy: Is there already a question testing the same rule/capability?
4. Validate YAML: `python -c "import yaml; yaml.safe_load(open(f))"`
5. Update `manifest.yaml` question counts

## File Structure

```
evaluation/
├── traj_analysis/           (gitignored — local analysis state)
│   ├── test/
│   │   ├── 2026-02.json
│   │   ├── 2026-04.json
│   │   └── 2026-05.json
│   └── prod/
│       ├── 2026-02.json
│       └── 2026-05.json
├── question_bank/
│   ├── platform/            (platform scope questions)
│   │   └── ig_agnostic.yaml
│   ├── input_generation/    (knowledge scope)
│   ├── structure_construction/
│   ├── scientific_analysis/
│   └── ...
└── skills/
    ├── evaluation-iteration/
    │   └── SKILL.md
    └── traj-analysis/
        └── SKILL.md          (this file)
```
