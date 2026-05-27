---
name: skill-writer
description: Write or refactor operator SKILL.md files. Use when writing a new skill from scratch, restructuring an existing skill for clarity, or reviewing skill quality. Covers section ordering, dependency analysis, reference routing, and information density principles.
---

# Skill Authoring

Write and refactor operator SKILL.md files that an agent reads in full on every invocation.

## Core Constraint

SKILL.md is loaded as a single tool_result block. The agent reads it top-to-bottom once, then acts. Implications:

- Every forward reference is a comprehension tax — a rule that mentions "OC22" before the heads table is defined forces the agent to hold an unresolved symbol.
- Repetition is noise, not reinforcement — under token pressure, duplicated content dilutes rather than strengthens.
- Length hurts the tail — content past ~120 lines competes with the agent's task context for attention. Move low-frequency detail to `references/`.

## Section Ordering — Dependency-First

Arrange sections so that every term, concept, or name used in a rule has already been **defined above it**. The ordering follows agent decision sequence:

```
1. Scope         — what this skill covers (1-3 lines)
2. Capability Gate  — pure STOP rules (no domain knowledge needed to evaluate)
3. Models / Resources — what's available, how to select
4. Task Scripts     — what scripts exist, their interfaces
5. Execution Workflow — step-by-step procedure tying it all together
6. Execution Rules  — detail rules referenced during execution
7. Error Patterns   — symptom → cause → action (consulted on failure only)
```

### Why This Order

| Step | Agent question | Depends on |
|------|---------------|------------|
| Scope | "Is this skill relevant?" | Nothing |
| Capability Gate | "Can I do this at all?" | Only Scope |
| Models | "What do I use?" | Scope |
| Task Scripts | "Which script, what args?" | Models (for --model/--head params) |
| Workflow | "In what order?" | Scripts + Models |
| Rules | "What details matter during execution?" | Scripts + Workflow |
| Errors | "What went wrong?" | All above |

### Hard Guards / Execution Rules — Before or After Workflow?

Constraint rules (e.g., "always set X", "never omit Y") exist in most operator skills. Their position relative to Workflow depends on their **nature**:

| Nature | Position | Rationale |
|--------|----------|-----------|
| **Pre-conditions** — agent must internalize BEFORE writing/acting (e.g., "cal_force 1 for relax") | Before Workflow | Agent loads constraints into working memory, then executes with them active |
| **Post-checks** — agent verifies AFTER completing a step (e.g., "run validator", "confirm file exists") | After Workflow | Agent finishes the step, then consults the checklist to catch errors |

If a guard is already enforced by a **validator script** that runs in the workflow, it does NOT need to appear as a pre-condition rule — the validator is the post-check. Only state rules in the skill body when:
1. Violating them causes **silent failure** (no error message, just wrong results), AND
2. No automated validator catches them.

### Workflow Organization — Self-Contained vs Separate Definitions

Two valid patterns for organizing workflow dependencies (tables, defaults, routing):

| Pattern | When to use | Example |
|---------|-------------|---------|
| **Self-contained** — each workflow step includes all information needed to execute it inline | Skill is long (200+ lines); information is step-specific and not shared across steps | Anthropic `mcp-builder`: Phase 1 includes URLs, framework choices, and examples directly in the step |
| **Separate definitions + reference** — tables/defaults defined before Workflow, steps say "per X above" | Skill is short (<100 lines); same table/value is referenced by multiple steps or by Gate | ABACUS: Routing table defined once, referenced by Workflow step 2 and also useful independently for orientation |

**Decision rule**: If the skill body is under ~100 lines and the LLM agent reads it all into context at once, separating definitions costs nothing (agent can "look up" without scrolling). If the skill is long (200+ lines), inline everything — content past the agent's attention window may be missed.

For short skills, separating shared definitions also makes maintenance easier — update one table instead of multiple inline copies.

### Dependency Analysis Checklist

Before finalizing order, for each rule ask:

1. What names/concepts does this rule reference?
2. Have those names been defined above?
3. If not → either move the rule down, or move the definition up.

If a logical section (e.g., "Decision Boundaries") contains rules with **different dependency levels**, split it:
- Rules depending only on scope → Capability Gate
- Rules depending on model/head concepts → place after Models

## Reference Routing — In-Place Pointers

References (`references/*.md`) hold low-frequency, detailed, or conditional content. Routing principle:

**Place the pointer at the exact sentence where the agent would need more detail.** Not in a separate "References" section — a centralized list says "all are equally relevant" and the agent won't know when to read which.

Pattern:
```markdown
## Key Rules
- DPA + LAMMPS freeze workflow → see `reference/dpa_lammps_freeze.md`
```

The agent encounters the pointer only when reading that rule, and only reads the reference file if executing that path.

### When to Keep in SKILL.md vs Move to Reference

| Keep in SKILL.md | Move to reference |
|-----------------|-------------------|
| Applies to >50% of tasks | Applies to <20% of tasks |
| 1-3 lines | Needs worked example or multi-step procedure |
| Hard guard (silent failure risk) | Nice-to-know detail |
| Defines a concept used by later rules | Standalone procedure |

## Information Density Rules

### Tables Over Prose

Bad (dense parenthetical chain):
```
OMat24 or Omat24 (default, inorganic — casing differs between model versions:
DPA3.2-5M uses OMat24, DPA3.1-3M/DPA2.4-7M use Omat24), OMol25 (organic), ...
```

Good (scannable sub-table):
```
| Head | Domain | Available on | Trigger |
|------|--------|-------------|---------|
| OMat24/Omat24 | inorganic | all | default |
| OC22 | surface/catalysis | DPA3.1+3.2 | Pt(111), CO ads |
```

### One Rule, One Location

If validate appears in Workflow Step 2, don't repeat it in a separate "Validation" section AND a "Routing Table" row. Redundancy passes the point of diminishing returns quickly — agent sees the first occurrence, treats repeats as noise.

Exception: if a rule applies in two genuinely different decision contexts (e.g., "relax-cell" matters both in "optimization" and "elastic" contexts), state it in both — but as a one-line reminder, not a full re-explanation.

### Concrete Over Abstract

Bad: "Ensure proper convergence parameters"
Good: "`--fmax 0.01` for optimization, `--fmax 0.05` for NEB"

Bad: "Validate the structure before submission"
Good: "`python scripts/validate_structure.py --structure <file>` — must PASS before Step 3"

### Executable Commands Over Descriptions

When a check can be expressed as a runnable command, give the command. The agent will copy-paste it. A description requires the agent to synthesize the command, introducing error:

```bash
python -c "from ase.io import read; ..."
```

beats "check that minimum image convention displacement is less than half the cell."

## STOP Rules (Capability Gate)

A STOP rule must be:
1. **Evaluable without domain knowledge from later sections** — agent can apply it knowing only the task request and scope
2. **Actionable** — tells agent exactly what to do (STOP + what to say)
3. **Exhaustive** — lists the forbidden cases, not "things like X"

Format:
```markdown
- **FORBIDDEN**: [list]. Action: STOP. Tell user "[message]". Wait for confirmation.
```

Anti-pattern: burying STOP conditions inside model selection rules or execution details. If the agent has to read 80% of the skill before encountering a gate, the gate arrives too late.

**No cross-skill name coupling**: a STOP rule should state *why* this skill
cannot handle the task, not *which other skill* to route to. Routing is the
system's job. Naming other skills creates maintenance coupling — if the target
skill is renamed or split, every referencing skill breaks silently.

Bad: `STOP → route to \`assemble-atomic-structure\``
Good: `STOP — this skill only transforms a single structure in-place.`

## Scripts Section

The Task Scripts table is the agent's lookup for "which tool do I call." Requirements:

- First column: script filename (agent uses this in `cmd`)
- Second column: full arg signature with `[]` for optional
- Third column: output filenames (agent needs these for chaining and workspace delivery)

Include format specs (e.g., `stages.json` schema) immediately after the table — the agent needs them when constructing the command.

## Operability Audit

After writing or reviewing a SKILL.md, audit every rule for agent executability. A rule is **operable** if the agent can act on it without inference, improvisation, or consulting undefined concepts.

### Test: Can the Agent Execute This?

For each rule, ask: "If I give this rule to an agent with no other context, can it produce the correct action deterministically?"

| Operable | Not operable |
|----------|-------------|
| "Run `dp --pt show <model> model-branch` to list heads" | "Do the internal check" |
| "`--fmax 0.01` for optimization" | "Ensure proper convergence" |
| "IF system contains surface + adsorbate → use OC22" | "When chemistry maps to a specialized head" |
| "System chemistry doesn't match any row above → STOP" | "For unfamiliar chemistry" |

### Common Operability Failures

**1. Undefined subject** — rule uses a concept never defined in the skill:
- "property class change" — what is a property class? Where is the list?
- "unfamiliar chemistry" — familiar to whom? Agent has no memory.

Fix: replace subjective terms with objective conditions or table lookups.

**2. Unresolved procedure** — rule says "do X" but X is not a concrete action:
- "first do the internal check" — what check? what command? where?
- "internal lookup and human choice" — lookup where?

Fix: replace with exact command or explicit step sequence.

**3. Decision requires inference** — rule gives principles instead of a lookup table:
- "chemistry maps to a documented specialized head" — agent must reason about what chemistry maps to what head.

Fix: provide a decision table with trigger conditions in the left column and action in the right.

**4. Scattered decision path** — agent needs information from 3+ locations to complete one action:
- Head selection requires: (a) head names from Models paragraph, (b) selection rules from Decision Boundaries, (c) verification command from Conditional Routing, (d) availability from `dp --pt show`.

Fix: consolidate into one self-contained section. All information needed for one decision lives in one place.

### Operability Audit Procedure

1. List every imperative statement (MUST, ALWAYS, DO NOT, run X, use Y)
2. For each, classify: ✓ operable / ✗ needs inference / ? ambiguous trigger
3. For each ✗ or ?: identify what's missing (undefined term? missing table? scattered info?)
4. Fix by: adding decision table, replacing vague term with condition, or consolidating scattered info

### Decision Table Pattern

When a rule requires choosing between options based on context, always use a decision table:

```markdown
| Condition (agent can observe) | Action |
|-------------------------------|--------|
| System has surface + adsorbate molecules | Use head OC22 |
| System is pure organic molecule | Use head OMol25 (DPA3.2 only) |
| System is MOF/porous framework | Use head ODAC23 |
| None of the above | Use OMat24 (DPA3.2) / Omat24 (DPA3.1) |
| Uncertain which row applies | Run `dp --pt show`, present options to user |
```

Left column must be **observable** (agent can determine from the input structure/prompt), not **interpretive** (requires domain expertise the agent may not have).

## Quality Checklist

Use when reviewing a finished SKILL.md:

- [ ] No forward references — every name is defined before use
- [ ] No redundant rules — each constraint stated once (one location owns it)
- [ ] Capability Gate has zero dependencies on later sections
- [ ] All references routed in-place (no orphan "References" section)
- [ ] Tables used for 3+ parallel items (not prose lists)
- [ ] Executable commands given where a check exists
- [ ] Total length ≤ 120 lines (excluding frontmatter/blank lines) — if over, audit what can move to reference
- [ ] Low-frequency paths (< 20% of tasks) in reference files, not SKILL.md body
- [ ] Every imperative rule passes operability test (agent can act without inference)
