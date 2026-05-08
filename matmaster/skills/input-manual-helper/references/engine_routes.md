# Engine Routes

Use this table to choose the input-preparation route. Engine-specific physical rules stay in the engine skill or in local validators; this file only defines which helper path prepares the files.

| Engine | Preparation route | Primary artifacts | Notes |
| --- | --- | --- | --- |
| ABACUS | `render_input.py` plus `diagnose_input.py`; use `--all-files` or `--output-dir` for complete runs | `INPUT`, `STRU`, `KPT` | Check the ABACUS skill for PP/orbital directories, SCF/NSCF chaining, and Bohrium defaults. |
| CP2K | `render_input.py` plus `diagnose_input.py` | `input.inp` or task-specific `.inp` | Check the CP2K skill for basis/potential choices, OT/KPOINTS constraints, and command defaults. |
| QE | `render_input.py` plus `diagnose_input.py` | `pw.in` | Check the Quantum ESPRESSO skill or official docs for cutoffs, occupations, K_POINTS, and workflow chaining. |
| ABINIT | `render_input.py` plus `diagnose_input.py` | `run.abi` | Check ABINIT references for ecut, ngkpt, nband, toldfe, and pseudopotential files. |
| LAMMPS | `render_input.py` plus `diagnose_input.py` | `.lammps` input script | Check the LAMMPS skill or docs for units, boundary, pair style, fix order, and run blocks. |
| ORCA | `render_input.py` plus `diagnose_input.py` | `.inp` | Check the ORCA skill for charge/multiplicity, PAL, TDDFT, RI/aux basis, and command defaults. |
| GROMACS | `render_input.py` plus `diagnose_input.py` for `.mdp`; package user topology and coordinates | `.mdp`, `.gro`, `.top`, optional `.tpr` | Check the GROMACS skill for grompp/mdrun staging and topology consistency. |
| PySCF | Do not render engine input; write a Python script directly | `run_pyscf.py`, structure file | Still check Python syntax, charge/spin, structure path, requested output fields, and manifest completeness. |

## Route Selection

- If the user supplied complete ready inputs, package and diagnose them instead
  of rendering new files.
- If the task needs an engine not listed here, stop and ask whether to use a
  template-only route or another skill.
- If an engine skill gives different execution defaults, follow the engine skill.
- If official documentation contradicts a local template, record the decision in
  the manifest assumptions.
