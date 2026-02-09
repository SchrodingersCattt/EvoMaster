---
name: log-diagnostics
description: Extract canonical error codes from VASP/LAMMPS log files for ResilientCalcExp. Use when a calculation job failed and you need to map the log to a fix strategy (e.g. scf_diverged -> update ALGO/AMIX). Scripts extract_error.py (usage: python extract_error.py <log_file_path>).
skill_type: operator
---

# Log Diagnostics Skill

Used by **ResilientCalcExp** and the agent to diagnose failed calculations without feeding full logs to the LLM.

## Scripts

- **extract_error.py** — Reads a log file path; outputs a single line error code (e.g. `scf_diverged`, `kpoints_error`, `grid_too_coarse`). Map this code to `config.mat_master.resilient_calc.error_handlers` for automatic fix actions.

## When to use

- After a job status is `Failed` or `Error`: run this script on the job’s OUTCAR or stderr to get the error code.
- Do **not** pass the entire log content to the agent; use this script to return only the code so that token usage stays low.

## Error codes (VASP)

| Code | Typical cause |
|------|----------------|
| scf_diverged | SCF not converging |
| scf_diagonalization_error | ZHEGV/ZPOTRF failure |
| kpoints_error | K-point or IBZKPT issue |
| grid_too_coarse | FFT grid |
| bond_atom_missing | Structural/input issue |
| unknown_error | No pattern matched |
