# Mat Master Prompts

Developer reference for the prompt files in this directory. **This file is not injected into any prompt.** It classifies rules into five layers and records where each concern lives after the refactor.

**Layers:**

- **constitution**: Role, task boundaries, mode behavior, compliance/safety, honesty/completion standards.
- **routing**: When to use which capability; technical Q&A vs long-form; which skill/tool is mandatory first.
- **tool affordance**: Non-obvious tool semantics (PDF-first, OSS URLs, auto-save, input constraints).
- **planner-only**: DEG schema, goal-oriented planning, step goal wording, planner-specific routing.
- **precheck-only**: Readiness assessment only; no planner/executor business rules.

---

## mat_master_system_prompt.txt

| Content | Layer |
| ------- | ----- |
| Role: Mat Master, EvoMaster for materials science | constitution |
| {{MAT_LANGUAGE_RULE}} | constitution |
| Goal + tool list ref ({{MAT_SW_LIST}}, {{MAT_SERVER_MAP}}) | constitution |
| Built-in tools list | constitution |
| {{MAT_TOOL_BLOCK}} | constitution |
| Workflow 1-4 (understand, plan, call, summarize) | constitution |
| Tools are for convenience; have limitations; encourage self-setup and hand-written code when needed | constitution |
| {{MAT_MODE_CONTRACT}} | constitution |
| Web search snippets; prefer English; source quality | tool affordance |
| Characterization routing (NMR/XRD/TEM MCP first) | routing |
| Run code in file; Python ModuleNotFoundError; retry different approach | tool affordance |
| MCP file args = OSS URLs; path adaptor; never fabricate URLs | tool affordance |
| When done use finish | constitution |
| Routing: technical Q&A vs written report | routing |
| PDF parsing: mat_doc first, no view/peek_file/Python first | tool affordance |
| User-uploaded files: read all before writing | constitution |
| Literature survey: deep-survey when report/file; Planner note | routing |
| Long-form writing: manuscript-scribe; Planner note | routing |
| Structure retrieval: structure-manager first; literature vs DB vs URL; validation | routing |
| Literature data table: lit-data-organizer before reporting | routing |
| Composition optimization: composition-optimization skill first | routing |
| {{MAT_EXEC_CONSTRAINTS}} {{MAT_CALC_RULES}} | constitution |
| Security/compliance: compliance-guardian before restricted execution | constitution |
| Safety: auth errors → try another approach; no credential scan. Ending: call finish; task_completed | constitution |
| {{MAT_FINAL_DOC_RULE}} | constitution |
| Final report: ## Execution Details structure; per-step; failed; approximations | constitution |

---

## tool_rules.txt

| Content | Layer | Note |
| ------- | ----- | ---- |
| Mode contract (Direct vs Planner) + multi-batch auto-save | constitution | Duplicate: keep in build_prompt _mode_contract only |
| Web search snippets; prefer English; source quality | tool affordance | Duplicate of mat_master |
| Characterization MCP routing (NMR/XRD/TEM) | tool affordance | Keep once in tool affordance |
| Routing technical Q&A vs writing; refs must have URL; concept rigor | routing | Duplicate of mat_master |
| PDF: mat_doc first | tool affordance | Duplicate |
| Mat MCP OSS URLs; path adaptor; model shortcuts; spin/charge DPA3.2; auto-download | tool affordance | Keep here (detailed) |
| structure-manager: get_info first | tool affordance | Keep |
| tasker-polar-surface: Type 1 vs 2/3; check_slab_tasker mandatory | tool affordance | Keep |
| lit-data-organizer: when to use; evidence persistence; PDF-first; resume | tool affordance | Keep (trim workflow detail to "use get_info") |
| Multi-batch: auto-saved outputs; final CSV from files; merge examples | tool affordance | Keep |
| composition-optimization: get_info first; branches; no local surrogate probe | tool affordance | Keep |
| input-manual-helper: local script generation, no hand-write; structure_file; validate_input; official docs | tool affordance | Keep |
| use_skill run_script: manuscript-scribe/deep-survey CLI details | tool affordance | Slim: minimal "use get_info; required args" in prompt; rest in skill |
| get_reference: reference_name = filename | tool affordance | Keep |
| edit/view path: absolute path | tool affordance | Keep |
| Review/survey: deep-survey first | routing | Keep once |
| Manuscript: retrieval before writing | tool affordance | Keep short |
| monitor_job failed: log_tail; diagnose; ask_human; abort | tool affordance | Keep |
| monitor_job task_intent mandatory | tool affordance | Keep |
| Final report Execution Details template | constitution | Duplicate; single source in executor constitution |
| Final document delivery (Planner vs Direct) | constitution | Duplicate; keep only in _mode_contract / constitution |

---

## planner_system_prompt.txt

| Section | Content | Layer |
| ------- | ------- | ----- |
| Role, MODE CONTRACT | Computational Research Architect; quality-first; validate/fix loops | planner-only |
| I | ENVIRONMENT & LICENSE FIREWALL; {{CRP_LICENSE_FIREWALL}}; Hardware | planner-only |
| II | FIDELITY (Screening vs Production) | planner-only |
| III | Resource gates; fallback_strategy | planner-only |
| IV | skill_evolution vs normal; do not specify tool names | planner-only |
| IV-B | Mandatory skill routing: deep-survey, manuscript-scribe (per-section, validate, assemble), structure-manager, lit-data-organizer, characterization MCP, composition-optimization | planner-only (goal-level routing) |
| IV-C | Paper reproduction: Step 1 parse PDF; no manuscript-scribe | planner-only |
| V | Step definition: goal-oriented; anti-slop; step_type; exceptions (name tools for routing) | planner-only |
| V-B | conditional_branch; depends_on | planner-only |
| V-C | Mid-execution revision | planner-only |
| VI | OUTPUT SCHEMA (JSON) | planner-only |
| Examples | APPROVED/REFUSED examples | planner-only |

Planner should not repeat executor-level tool usage details (e.g. full manuscript-scribe CLI). Keep only: "include step whose goal says use **manuscript-scribe** / **deep-survey** / …" and schema.

---

## pre_check_system_prompt.txt

| Content | Layer |
| ------- | ----- |
| Role; assess readiness vs prerequisites | precheck-only |
| Assessment criteria (uploaded files, task clarity, required context) | precheck-only |
| OUTPUT FORMAT (JSON schema) | precheck-only |
| Notes on search_info prerequisites | precheck-only |
| RULES (when ready_to_plan true/false; do not generate plan) | precheck-only |

No planner or executor business rules. Optional addition: "Only assess readiness; do not introduce planner/executor specifics."

---

## user_prompt.txt

Task shell only (task_id, task_type, description, input_data). No rule classification.

---

## Deduplication summary

- **Mode contract + final document delivery**: Only in `build_prompt._mode_contract()` and injected into executor; remove from tool_rules.txt.
- **Routing (technical Q&A vs writing)**: Keep in executor constitution/routing once; remove duplicate from tool_rules or keep minimal reminder in tool affordance.
- **PDF-first, characterization routing, structure-manager, lit-data-organizer, composition-optimization**: Keep in executor as short routing anchors; keep tool-affordance details (OSS, auto-save, tasker, etc.) only in tool_rules.
- **Final report (Execution Details)**: Single source in executor constitution; remove from tool_rules.
- **Planner**: Remove lengthy executor-style CLI/workflow details; keep DEG schema and goal-level skill/tool names for routing.
