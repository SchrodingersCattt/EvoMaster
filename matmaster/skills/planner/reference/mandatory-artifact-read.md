# Mandatory Artifact Read

Before creating or revising any planning artifact, read the relevant existing context first. A fresh agent must be able to reconstruct state from the artifacts alone — assume no chat history is available to a future session.

## Required read order

1. **Project-level instructions** — user rules, `CLAUDE.md` / `AGENTS.md`, local conventions, team style guides.
2. **Existing artifacts for the same topic**:
   - `docs/<topic-slug>/SPEC.md`
   - `docs/<topic-slug>/ACCEPTANCE.md`
   - `docs/<topic-slug>/PLAN.md`
   - `docs/<topic-slug>/plans/*.md`
3. **Referenced appendices and data** linked from the artifacts — baseline tables, parameter catalogs, research notes, prior reports.
4. **Materials-computation project surface** when it may affect scope, method, or validation:
   - workflow definitions (`atomate`, `AiiDA`, `custodian`, Snakemake, DPDispatcher jobs).
   - input templates and examples (`INCAR`, `POSCAR`, `STRU`, `INPUT`, `pw.in`, `cp2k.inp`, LAMMPS scripts).
   - prior run logs and deliverables (`OUTCAR`, `scf.log`, `OSZICAR`, notebooks, CSV summaries).
   - environment, container image, or module specs (conda env, Singularity, Docker tag).
   - queue / submission scripts (SLURM, LSF, PBS, Bohrium job specs).
   - figure or data-package precedents the user has accepted before.
5. **User-provided references** — DOIs, arXiv IDs, internal memos, figures — that materially shape scope, method choice, or acceptance thresholds.

## Rules

- Prefer updating an existing artifact over creating a duplicate.
- Never start from a blank artifact if a matching one already exists.
- If expected files are missing, say so explicitly — do not silently fill gaps.
- If artifacts conflict, surface the conflict; do not silently pick one.
- Never assume a future session has access to the current chat history.
