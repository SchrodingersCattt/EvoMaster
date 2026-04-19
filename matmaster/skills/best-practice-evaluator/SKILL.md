---
name: best-practice-evaluator
description: "Evaluate computational materials science workflows, DFT input files, and simulation setups against established best practices. Use for any task that asks to review, assess, audit, or evaluate the quality/correctness of a computational setup, input file, or workflow."
skill_type: operator
---

# Best Practice Evaluator Skill

Systematic evaluation of computational materials science workflows, DFT input files, and simulation setups against established best practices.

## Trigger Conditions

- Task asks to "evaluate", "review", "assess", "audit", or "check" a computational setup
- Task involves reviewing DFT input parameters for correctness or quality
- Task asks whether a workflow follows best practices or guidelines
- Task asks to identify issues, mistakes, or improvements in simulation input files
- Task involves comparing a setup against standard recommendations

## Evaluation Workflow

1. **Read all input files** completely before making any assessments. Never skip files.
2. **Identify the software and task type** — Determine which computational code (ABACUS, CP2K, QE, VASP, LAMMPS, etc.) and what calculation type (SCF, relax, band, MD, etc.).
3. **Systematic checklist evaluation** — Walk through ALL items in `references/dft_best_practices.md` that apply to the identified software and task type. Check each item explicitly.
4. **Report findings structured by category**:
   - **Critical issues** (will cause failure or wrong results)
   - **Best practice violations** (suboptimal but may still run)
   - **Recommendations** (improvements for better accuracy/efficiency)
   - **Correct practices** (what the setup does well)
5. **Provide specific fixes** — For each issue found, state the exact parameter/value that should be changed and why.

## Hard Constraints

- **Systematic, not ad hoc**: Always use the structured checklist from `references/dft_best_practices.md`. Do not rely on spotting issues by eye alone — walk through every applicable category.
- **Evidence-based**: Every finding must cite the specific parameter value found in the input file and the standard it violates.
- **Completeness over speed**: Check ALL applicable categories even if obvious issues are found early. A review that misses categories is incomplete.
- **No fabrication**: If a parameter is not present in the input file, say so explicitly. Do not assume default values unless the software documentation specifies them.
- **Actionable output**: Every issue must include a specific fix (parameter name, recommended value, and reason).
- **Separate critical from advisory**: Always distinguish between issues that cause failure/wrong results vs. issues that are suboptimal but workable.

## When to Use

- "Review this ABACUS input" → full checklist evaluation
- "Is this DFT setup correct?" → systematic check against standards
- "What best practices are violated?" → comprehensive audit
- "Evaluate this computational workflow" → workflow-level review
- "Check if convergence parameters are adequate" → parameter quality assessment
