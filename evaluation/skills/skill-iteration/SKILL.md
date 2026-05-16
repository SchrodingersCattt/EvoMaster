---
name: skill-iteration
description: Diagnose why eval questions fail from agent trajectory, then fix the right layer (skill docs, validation scripts, or skill triggers). Load after `evaluation-iteration` rules out criteria bugs, when eval results show agent capability failures that trace back to missing knowledge or inadequate tooling.
---

# Skill Iteration

Close the loop from eval failure → trajectory diagnosis → skill/tool fix → re-verify.

## Diagnosis Decision Tree

```
eval question fails (agent capability, not criteria bug)
│
├─ Agent never loaded the relevant skill?
│   └─ Fix: skill `description` field lacks triggering keywords
│         from the eval prompt. Add them.
│
├─ Agent loaded skill but the knowledge isn't documented?
│   └─ Fix: add the missing rule/fact to the skill.
│         → See "Where to Add Knowledge" below.
│
├─ Agent read the correct rule but generated output that violates it?
│   └─ Fix: strengthen the validation tool to catch the violation.
│         Agent trusts "All checks passed" — if the tool misses it,
│         the agent won't self-correct.
│         → See "Fixing Validation Scripts" below.
│
├─ Agent read the rule but misunderstood / applied it wrong?
│   └─ Fix: rewrite the rule with explicit DO NOT + concrete example.
│         Ambiguous phrasing causes misapplication.
│
└─ Agent cannot do this task even with perfect docs (model limitation)?
    └─ Record as known limitation. Consider:
       - Providing data_files to reduce task complexity
       - Adding a hint in prompt (lowers difficulty — document why)
```

## Where to Add Knowledge

Choose placement by severity:

| Severity | Where | Format | Agent sees it... |
|----------|-------|--------|------------------|
| Hard guard (silent failure if violated) | `SKILL.md` → Hard Guards section | `- **Bold title**: explanation.` | Every time skill loads |
| Task-type rule | `SKILL.md` → Task-Specific Deltas | `- task_type: include param X.` | Every time skill loads |
| Detailed procedure / worked example | `references/*.md` (new or existing) | Markdown section with code blocks | Only when agent reads that reference |
| Rare edge case | `references/troubleshooting.md` | Entry in decision tree or FAQ | Only when agent troubleshoots |

**Key principles:**

- If the rule is short (1-2 sentences) and applies broadly → put in `SKILL.md` directly
- If it needs a worked example or multi-step procedure → put in `references/` and update the SKILL.md reference index with an accurate `*Applies to*` line
- Never bury critical guards in reference files only — duplicate the one-liner in SKILL.md Hard Guards and point to the reference for details

## Fixing Validation Scripts

Validation scripts (e.g., `validate_input.py`) are the last line of defense before submission. When the agent generates output that violates a rule but validation passes:

**Diagnosis pattern:**
1. Agent trajectory shows: Write → validate → "All checks passed" → submit
2. The written file has a defect the script didn't catch

**Fix checklist:**
- [ ] Identify the missing check (e.g., "ntype not present" vs "ntype wrong value")
- [ ] Add the check with a FAIL message that includes the fix hint (e.g., `"Must set ntype=2 to match STRU"`)
- [ ] Ensure the check covers both "wrong value" AND "missing entirely" cases
- [ ] Test the fix against the actual failing artifact from the eval run

**FAIL message format:**
```
FAIL {prefix}: {what is wrong}. {what the correct value should be and why}.
```

Good: `FAIL [INPUT_vac_bsse]: ntype is missing from INPUT. Must set ntype=2 to match STRU ATOMIC_SPECIES count.`
Bad: `FAIL: ntype error`

The agent reads this message and self-corrects — a clear fix suggestion is essential.

## Fixing Skill Trigger (Agent Didn't Load Skill)

If the agent never called `Skill: <name>`, check:

1. **`description` field** in the skill's YAML frontmatter — does it contain keywords the agent would see in the eval prompt?
2. **Exp tool list** — is the skill registered in the exp config's enabled skills?
3. **Prompt phrasing** — does the eval prompt use terminology that differs from the skill description?

Fix: add relevant keywords/synonyms to the skill `description` field.

## Fixing Misunderstanding (Agent Read But Applied Wrong)

Signs: agent's trajectory shows it read the correct reference section, reasoned about it, but produced wrong output.

**Fix approaches (combine as needed):**

1. **Add "DO NOT" counter-example** right after the rule:
   ```
   - Use `calculation scf` for ASE-driven workflows.
     DO NOT use `calculation relax` — ABACUS internal optimizer conflicts with ASE.
   ```

2. **Add explicit mapping table** when the rule requires choosing between options:
   ```
   | Scenario | calculation value |
   |----------|------------------|
   | Standalone relax | relax |
   | ASE-driven relax | scf |
   | Standalone MD | md |
   | ASE-driven NEB | scf |
   ```

3. **Add the "why"** — agents follow rules better when they understand the mechanism:
   ```
   ASE handles the optimization loop externally; ABACUS only computes
   single-point energy and forces per step.
   ```

## Verification

After applying fixes:

1. **Minimal check**: manually inspect that the new content is in the right place and correctly formatted
2. **Eval re-run**: re-run the specific failing question (`--questions <id>`) with k=1
3. **Pass criteria**: the fix is verified when:
   - Agent's trajectory shows it read the new content
   - Agent's output passes the previously-failed checklist item
4. **Regression check**: if you modified a validation script, ensure existing passing questions still pass (`--slices @<tag>` with k=1)

## Examples

### Example 1: Missing ntype (validate_input.py gap)

- **Symptom**: IG_abacus_002 fails `must_input_vac_ntype_matches_species`
- **Traj**: agent wrote correct STRU with 2 species, INPUT omits `ntype`
- **Root cause**: `validate_input.py` only checked ntype when present, didn't report missing ntype
- **Fix**: added `else` branch in validate_input.py to FAIL on missing ntype with fix hint
- **Classification**: "Agent read rule but generated output that violates it" → fix validation tool

### Example 2: ASE-ABACUS calculation mode (skill knowledge gap)

- **Symptom**: IG_abacus_012 fails `must_relax_input_core` (expects `calculation scf`)
- **Traj**: agent wrote `calculation relax` for ASE-driven relaxation stage
- **Root cause**: skill docs had no mention of ASE Calculator mode requiring `calculation scf`
- **Fix**: added ASE-ABACUS hard guard to SKILL.md
- **Classification**: "Agent loaded skill but knowledge isn't documented" → add to skill
