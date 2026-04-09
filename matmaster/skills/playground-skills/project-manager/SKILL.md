---
name: project-manager
description: "Orchestrate multi-step computational materials science projects. Handles task decomposition, dependency ordering, progress tracking, and deliverable assembly for complex workflows spanning structure preparation, calculation submission, result analysis, and reporting."
skill_type: orchestrator
---

# Project Manager Skill

Orchestrates complex, multi-step project workflows where the user requests a complete investigation (e.g., "compute adsorption energies for 5 surfaces", "compare band gaps across a material series", "screen compositions for target property").

## When to use

- User requests a **multi-step project** involving 3+ distinct phases (e.g., structure prep → calculation → analysis → report)
- Task mentions "project", "study", "investigation", "screening", "comparison", or "systematic"
- Multiple deliverables are expected (structures, input files, results, figures, summary)
- Work spans multiple skills (structure-manager, abacus, bohrium-job, result-analysis, etc.)

## Workflow

### Phase 1: Plan
1. **Decompose** the project into atomic tasks. Each task = one clear deliverable (a file, a calculation result, a plot).
2. **Order by dependency**: identify which tasks block others (e.g., slab must exist before adsorbate placement).
3. **Identify parallelism**: tasks with no mutual dependency can run concurrently (e.g., build multiple slabs simultaneously).
4. **Estimate budget**: count expected turns/steps; if > 20 sub-tasks, propose phased delivery.

### Phase 2: Execute (breadth-first)
1. **Save-early**: produce and save each intermediate deliverable as soon as ready. Never hold outputs in memory across many steps.
2. **Breadth-first batching**: when N similar tasks exist (N >= 3), batch them. Use `--batch` modes of available scripts rather than sequential single calls.
3. **Fail-forward**: if one sub-task fails, log the failure and continue with independent tasks. Do not let one failure block the entire project.
4. **Checkpoint**: after completing each phase (all structures built, all calculations submitted, etc.), summarize progress before moving to the next phase.

### Phase 3: Assemble & Report
1. **Collect results**: gather all outputs from sub-tasks into a unified summary.
2. **Derive quantities**: compute derived properties (surface energy, formation energy, band gap trends, etc.) using formulas with explicit numerical values.
3. **Visualize**: produce comparison plots/tables when the project involves a series.
4. **Final deliverable**: write a concise project summary — key findings, deliverable file list, any limitations or failures encountered.

## Hard constraints

- **Deliverable-first**: at every checkpoint, ensure all completed deliverables exist as files in the workspace. A plan or spec is not a deliverable.
- **No silent failures**: every failed sub-task must be reported with reason. Do not silently skip.
- **Consistent parameters**: when comparing across a series, keep calculation settings (basis, cutoff, k-mesh, functional) identical unless the comparison specifically varies them.
- **Token economy**: prefer batch script calls over sequential individual calls. Prefer saving files immediately over accumulating in memory. Prefer tables over prose for multi-item results.

## Integration with other skills

| Phase | Primary skills |
|-------|---------------|
| Structure prep | structure-manager, mcp-mat-sg, tasker-polar-surface |
| Input generation | abacus, input-manual-helper, cp2k, lammps |
| Submission | bohrium-job |
| Result parsing | result-analysis |
| Visualization | result-analysis (plot_publication.py) |

Invoke each skill as needed; this skill provides the orchestration layer, not replacement for domain skills.
