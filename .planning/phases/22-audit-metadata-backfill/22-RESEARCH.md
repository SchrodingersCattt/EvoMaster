# Phase 22: Audit Metadata Backfill - Research

**Researched:** 2026-03-30
**Domain:** Planning artifact metadata consistency / audit traceability
**Confidence:** HIGH

## Summary

Phase 22 is a pure documentation/metadata phase. No code changes are needed. The scope is to fix inconsistencies between planning artifacts (SUMMARY.md frontmatter, REQUIREMENTS.md, PROJECT.md, STATE.md, ROADMAP.md, MILESTONE-AUDIT.md) so that a re-audit produces zero documentation gaps.

The investigation found that REQUIREMENTS.md was already substantially fixed during the gap closure planning commit (bbfdada). The remaining work concentrates on three categories: (1) SUMMARY.md frontmatter with incorrect `requirements-completed` claims, (2) PROJECT.md stale context/counters, and (3) STATE.md stale focus description. The audit file itself (.planning/v2.0-MILESTONE-AUDIT.md) is currently untracked and needs to be committed as well.

**Primary recommendation:** Fix all identified metadata discrepancies in a single pass, then verify by re-reading the audit's `documentation_gaps` list item-by-item.

## Artifact Gap Inventory

### Category 1: SUMMARY.md Incorrect requirements-completed Claims

These SUMMARY files claim requirements that were NOT actually completed in their respective plans. The audit (v2.0-MILESTONE-AUDIT.md) explicitly flags these discrepancies.

| File | Current Claim | Problem | Correct Value |
|------|---------------|---------|---------------|
| `14-01-SUMMARY.md` | `[TOOL-01, TOOL-02, TOOL-03, TOOL-04]` | TOOL-02 NOT implemented -- BashTool uses asyncio.to_thread, not create_subprocess_exec. 14-VERIFICATION.md line 85 confirms. | `[TOOL-01, TOOL-03, TOOL-04]` |
| `14-02-SUMMARY.md` | `[TOOL-01, TOOL-05]` | TOOL-05 was intentionally deferred to Phase 18. Summary body explicitly documents deferral. | `[TOOL-01]` |
| `15-02-SUMMARY.md` | `[HOOK-02]` | Implementation was reverted during merge (commit c60329b). Current HEAD has old queue.Queue version. Phase 20 will re-implement. | `[]` with note: "HOOK-02 reverted -- re-assigned to Phase 20" |

**Confidence:** HIGH -- all three discrepancies documented in v2.0-MILESTONE-AUDIT.md with specific code evidence.

### Category 2: PROJECT.md Stale Content

| Location | Current | Correct | Severity |
|----------|---------|---------|----------|
| Line 40: Active section | "35 items, 15 complete after Phase 15" | "35 items, 33 complete after Phase 19" | Misleading counter |
| Line 69: Current State | "Phase 19 complete" (correct) but was last updated 2026-03-28 with Phase 16 content | Should reflect Phase 17-19 completions | Stale context |
| Line 155: Last updated | "2026-03-28 after Phase 16 complete" | Should say Phase 19 | Stale timestamp |
| Lines 34-36: Validated | Hook/Exp/Spawn entries reference Phase 15/18 correctly | Already correct | No action |

**Confidence:** HIGH -- direct comparison between file content and actual state.

### Category 3: STATE.md Minor Staleness

| Location | Current | Correct |
|----------|---------|---------|
| Line 21: PROJECT.md reference | "updated 2026-03-26" | Will become current date after PROJECT.md update |
| Line 24: Current focus | "Phase 19 -- Service layer bridge" | Should reflect Phase 20-22 gap closure status |

**Note:** STATE.md is typically auto-updated by GSD tooling during phase transitions. The planner should update it if Phase 22 is the final gap closure phase, or leave it for the milestone completion workflow.

**Confidence:** HIGH.

### Category 4: Audit File Tracking

The `.planning/v2.0-MILESTONE-AUDIT.md` file exists but is currently **untracked in git** (shown in git status as `??`). It should be committed as part of Phase 22 to preserve audit history.

**Confidence:** HIGH -- verified from git status.

### Category 5: REQUIREMENTS.md (ALREADY FIXED)

The audit originally identified 7 documentation gaps:
- INFR-01/02/03: checkbox `[ ]` and traceability `Pending`
- EXPL-01/02/03: traceability `Pending`
- TOOL-05: checkbox `[ ]` and traceability `Pending`
- Coverage summary stale (15 vs 33)

**Current state:** ALL of these were fixed in commit bbfdada ("docs(roadmap): add gap closure phases 20-22"). Current REQUIREMENTS.md shows:
- All checkboxes correct (`[x]` for complete, `[ ]` for TOOL-02 and HOOK-02 only)
- All traceability entries correct (Complete/Pending matches reality)
- Coverage summary reads "33 (all except TOOL-02, HOOK-02)" -- correct

**No action needed for REQUIREMENTS.md.**

**Confidence:** HIGH -- verified by reading current file content.

## Architecture Patterns

### Frontmatter Format

All SUMMARY.md files use YAML frontmatter with this structure for requirement tracking:

```yaml
---
phase: {phase-slug}
plan: {plan-number}
# ... other fields ...
requirements-completed: [REQ-ID-1, REQ-ID-2]
# Metrics
duration: {Xmin}
completed: {date}
---
```

The `requirements-completed` field is a YAML list of requirement IDs. It is the primary source the audit workflow checks when cross-referencing SUMMARY claims against VERIFICATION evidence.

### Audit Cross-Reference Model

The milestone audit uses a 3-source cross-reference for each requirement:

```
VERIFICATION.md (code evidence) + SUMMARY.md frontmatter + REQUIREMENTS.md checkbox/traceability
```

All three must agree for a requirement to show "satisfied" without documentation gap flags. Phase 22's goal is to make SUMMARY frontmatter match VERIFICATION truth.

### Recommended Edit Pattern

For SUMMARY.md corrections:
1. Edit ONLY the `requirements-completed` YAML frontmatter line
2. Add a `# Metadata correction` comment block below the frontmatter close `---` if the change is non-obvious
3. Do NOT change the summary body content -- it accurately describes what was attempted/achieved at the time

For PROJECT.md updates:
1. Update the stale counter in Active section
2. Update Current State paragraph with Phase 17-19 outcomes
3. Update last-updated timestamp
4. Do NOT restructure -- maintain existing format

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Audit re-verification | Manual re-reading of all artifacts | Checklist against v2.0-MILESTONE-AUDIT.md `documentation_gaps` section | The audit already identified every gap; just use its list as the checklist |
| Requirement cross-reference | Manual scanning of 17 SUMMARY files | Focused fixes on the 3 files identified by audit | Only 3 SUMMARY files have discrepancies; the other 14 are correct |

## Common Pitfalls

### Pitfall 1: Over-Editing SUMMARY Body Content
**What goes wrong:** Temptation to rewrite SUMMARY descriptions to match current state
**Why it happens:** SUMMARY body may describe aspirations that weren't fully achieved
**How to avoid:** ONLY edit the `requirements-completed` frontmatter field. The body is a historical record of what the plan execution session believed it accomplished. VERIFICATIONs are the truth layer.
**Warning signs:** Editing anything between the closing `---` and the end of the file beyond adding a correction note

### Pitfall 2: Forgetting That Phase 20/21 Will Add New Claims
**What goes wrong:** Trying to "pre-assign" TOOL-02 or HOOK-02 to Phase 20/21 SUMMARY files that don't exist yet
**Why it happens:** Desire for completeness
**How to avoid:** Phase 22 only fixes HISTORICAL metadata. Phase 20/21 will create their own SUMMARY files with correct claims when they execute. REQUIREMENTS.md traceability already correctly maps TOOL-02 -> Phase 21, HOOK-02 -> Phase 20.
**Warning signs:** Creating or editing files in phases/20-* or phases/21-*

### Pitfall 3: Audit File Becoming Stale After Fixes
**What goes wrong:** Fixing the gaps but not noting that the audit's `documentation_gaps` section is now resolved
**Why it happens:** Audit file treated as read-only historical record
**How to avoid:** The audit file itself should get a brief addendum or the frontmatter `status` should update from `gaps_found` to reflect partial resolution (remaining gaps are code, not docs). This is optional but clean.
**Warning signs:** Re-audit still flagging documentation_gaps that were already fixed

## Exact Change List

The planner should create tasks for these exact edits:

### Edit 1: `14-01-SUMMARY.md` frontmatter fix
```yaml
# FROM:
requirements-completed: [TOOL-01, TOOL-02, TOOL-03, TOOL-04]
# TO:
requirements-completed: [TOOL-01, TOOL-03, TOOL-04]
```

### Edit 2: `14-02-SUMMARY.md` frontmatter fix
```yaml
# FROM:
requirements-completed: [TOOL-01, TOOL-05]
# TO:
requirements-completed: [TOOL-01]
```

### Edit 3: `15-02-SUMMARY.md` frontmatter fix
```yaml
# FROM:
requirements-completed: [HOOK-02]
# TO:
requirements-completed: []
```
Add note after frontmatter: HOOK-02 implementation (asyncio.Future) was reverted during merge (commit c60329b). Re-assigned to Phase 20.

### Edit 4: `PROJECT.md` stale content update
- Line 40: Update "15 complete after Phase 15" to "33 complete after Phase 19"
- Line 69 (Current State): Update to reflect Phase 17-19 completions and gap closure status
- Line 155: Update timestamp

### Edit 5: Commit `v2.0-MILESTONE-AUDIT.md`
- Stage and commit the audit file as part of this phase's deliverables

### Edit 6 (optional): `v2.0-MILESTONE-AUDIT.md` addendum
- Update `documentation_gaps` section to note which items were resolved by this phase and prior commits
- Update frontmatter status if desired

## Verification Checklist

After all edits, verify by walking through the audit's documentation_gaps list:

| Audit Gap | Current State | Phase 22 Action |
|-----------|---------------|-----------------|
| "REQUIREMENTS.md traceability: INFR-01/02/03 marked Pending" | ALREADY FIXED (commit bbfdada) | None |
| "REQUIREMENTS.md traceability: EXPL-01/02/03 marked Pending" | ALREADY FIXED (commit bbfdada) | None |
| "REQUIREMENTS.md traceability: TOOL-05 marked Pending" | ALREADY FIXED (commit bbfdada) | None |
| "REQUIREMENTS.md checkboxes: INFR-01/02/03 still [ ]" | ALREADY FIXED (commit bbfdada) | None |
| "REQUIREMENTS.md coverage summary at bottom is stale" | ALREADY FIXED (commit bbfdada) | None |
| "Multiple SUMMARY.md files missing requirements-completed frontmatter" | Misleading wording -- no SUMMARY lacks the field, but 3 have INCORRECT values | Edit 1, 2, 3 |
| PROJECT.md stale counters/context | Still stale | Edit 4 |
| Audit file untracked | Still untracked | Edit 5 |

## State of the Art

| Old State | Current State | Changed By | Impact |
|-----------|---------------|------------|--------|
| REQUIREMENTS.md had 7 sync gaps | All 7 fixed | Commit bbfdada | No REQUIREMENTS.md work needed in Phase 22 |
| 14-01-SUMMARY falsely claims TOOL-02 | Still false | -- | Must fix |
| 14-02-SUMMARY falsely claims TOOL-05 | Still false | -- | Must fix |
| 15-02-SUMMARY claims HOOK-02 (regressed) | Still false | -- | Must fix |
| PROJECT.md says 15 complete | Still says 15 | -- | Must update |

## Open Questions

1. **Should the audit file be updated or left as historical record?**
   - What we know: The audit accurately describes gaps at audit time (2026-03-29). Some gaps were already fixed.
   - What's unclear: Whether to update the audit frontmatter/body or keep it as-is and let Phase 22 SUMMARY describe what was fixed.
   - Recommendation: Add a brief addendum section at the bottom noting which documentation_gaps were resolved and by what commit. Do not rewrite the audit body.

2. **Should STATE.md be updated in Phase 22?**
   - What we know: STATE.md is normally auto-managed by GSD tooling.
   - What's unclear: Whether manual edit is appropriate or should wait for `/gsd:transition`.
   - Recommendation: Leave STATE.md for the milestone completion workflow. Only update if Phase 22 is definitively the last phase before milestone close.

## Sources

### Primary (HIGH confidence)
- `.planning/v2.0-MILESTONE-AUDIT.md` -- full gap inventory, cross-referenced against code
- `.planning/REQUIREMENTS.md` -- current state of all 35 requirements
- `.planning/ROADMAP.md` -- phase status and dependency map
- `.planning/PROJECT.md` -- stale counters identified by direct reading
- All 17 v2.0 SUMMARY.md files (phases 12-19) -- frontmatter scanned via grep
- `14-VERIFICATION.md` -- TOOL-02 discrepancy documented with code evidence

### Secondary (MEDIUM confidence)
- Git log for REQUIREMENTS.md -- confirms bbfdada already fixed 7 gaps

## Metadata

**Confidence breakdown:**
- Artifact gaps: HIGH -- every discrepancy traced to audit evidence + current file state
- Fix actions: HIGH -- each fix is a single-line YAML edit or paragraph update
- Completeness: HIGH -- exhaustive scan of all 17 v2.0 SUMMARY files confirmed only 3 have issues

**Research date:** 2026-03-30
**Valid until:** Until Phase 20/21 execute (they may create new SUMMARY files that need their own correct frontmatter)
