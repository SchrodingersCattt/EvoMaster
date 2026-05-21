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

**Important: Do NOT skip sessions before analysis.** Every session that passes the filter above must be fully pulled and analyzed — including developer sessions and non-materials-science sessions. The verdict (`skip`/`informational`/`actionable`) is an *output* of analysis, not a pre-filter. Even a "simple Q&A" or "off-topic" session can reveal agent behavior problems (hallucination, unnecessary tool calls, poor refusal handling).

**Analyze in strict chronological order.** Always continue from where the last analysis left off (check existing `traj_analysis/{env}/` files for the latest `max_event_id` or session timestamp). Never jump to recent months while older sessions remain unanalyzed.

**Analyze thoroughly.** Do not just glance at tool call names and final responses. For each session, read through the intermediate tool call arguments and results to assess:
- Did the agent pick the right tool/approach on the first try, or did it fumble?
- Were there unnecessary detours (loading irrelevant skills, redundant reads, exploratory commands that led nowhere)?
- Did the agent waste turns on think/deliberation without acting?
- Was the tool call sequence logical and direct, or did the agent backtrack?
- For multi-step workflows, did the agent plan ahead or repeatedly discover what to do next?

The goal is to identify *smoothness* issues — not just whether the task completed, but whether it completed efficiently and without friction.

**Pay special attention to retries and non-smooth behavior.** Any time a session is not smooth — retries, backtracking, parameter guessing, unnecessary detours — analyze why and consider whether an eval question could expose and drive improvement. Don't limit yourself to "knowledge gaps"; any friction point is a candidate:
- Agent retried the same tool 3+ times with different parameters
- Agent took a roundabout path when a direct one existed
- Agent wasted turns on exploration before acting
- Agent used wrong tool/approach first, then corrected

**Always report fallback/recovery behavior explicitly.** When a tool or MCP service fails (timeout, error, missing package), the analysis MUST describe:
1. What failed and why (timeout, missing dep, infra error)
2. What the agent did next (retry? install? fallback to alternative? give up? ask user?)
3. Whether the fallback was appropriate and timely
4. Whether the user was informed of the degradation

Do NOT mark these sessions as "skip" — they reveal important agent resilience patterns. Report them as informational with a note on the recovery quality (good/poor/absent).

For each friction point: consider whether an eval question with a tight turn_budget would force the agent to get it right on the first try. These questions directly reduce wasted turns in production.

**Eval threshold: "first-try correct", not "eventually correct".** The goal is for the agent to get it right on the first attempt. Self-correction is better than persistent error, but it still indicates a knowledge gap worth testing. Specifically:
- Agent initially used wrong parameters/values but later self-corrected → **actionable** (should know the correct value upfront)
- Agent tried wrong tool/approach first, then pivoted to correct one → **actionable** (should pick the right approach immediately)
- Agent got wrong intermediate result, noticed it was unphysical, and fixed → **actionable** (should compute correctly the first time)
- Agent chose a suboptimal method that still produces acceptable results (e.g., SCF instead of relax, minimal basis set) → **informational** unless it leads to clearly wrong answers

Only exclude from eval consideration:
- Pure infrastructure failures (storePath, auth, network) where agent had no choice
- Tool API limitations agent cannot work around (MCP tool doesn't expose a parameter)
- Runtime-dependent issues that cannot be stably reproduced in eval (web search results, node availability)

**Skill documentation does not guarantee compliance.** Even if a skill clearly documents the correct approach (e.g., "use OC22 head for surface adsorption"), assume the agent might not follow it. Every critical decision point documented in skills should have a corresponding eval question to verify the agent actually applies that knowledge. The eval question ensures the skill works end-to-end, not just exists on paper.

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
- `skip`: No agent behavior to analyze (infra failure before any agent action, off-topic non-materials-science query, empty/cancelled session with zero tool calls)
- `informational`: Agent executed but no new eval question needed. Sub-categories:
  - `covered`: Issue already tested by an existing eval question (cite the question ID in `eval_recommendation`)
  - `untestable`: Problem exists but cannot be tested via eval (system bugs like storePath/finish-gate/context-compaction, runtime-dependent issues like web search result quality, efficiency problems without knowledge gaps)
- `actionable`: Should generate a new evaluation question (exposes a testable domain knowledge gap or methodology error not covered by existing questions)

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

### 5. Settle Release Gate Cases

`evaluation/release_gate/cases.yaml` 维持固定 **15 个 case** 的回归集。每个 case 测试不同的能力点，用于上线前端到端回归。

**总量控制：新增一个必须同时下掉一个。** 下掉的标准：
- 该能力点已被新 case 覆盖（合并）
- 该 case 已经连续多次通过且不再有区分度
- 该 case 的 friction 已被 skill/prompt 修复，不再是痛点

**Criteria for inclusion:**
- Task is a standard materials science computation workflow (structure build, DPA/ABACUS calc, property analysis)
- Moderate complexity (would take 5-30 tool calls if done smoothly)
- Not dependent on user-specific custom skills or private data
- Each case tests a **distinct capability point** — no two cases should test the same thing
- If user files are needed, an OSS-accessible copy must exist or be created

**Format:**
```yaml
  - id: rg_NN
    title: "short title"
    prompt: "user prompt as they would naturally ask"
    source_session: "session_id"
    notes: "what capability this tests; what went wrong on prod (if any)"
```

### 6. Review Cycle

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
├── release_gate/
│   └── cases.yaml           (e2e regression cases from real sessions)
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
