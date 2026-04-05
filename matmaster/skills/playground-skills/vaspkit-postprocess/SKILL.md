---
name: vaspkit-postprocess
description: "VASPKIT post-processing for VASP outputs: K-path (303/302), band structure (211), hybrid-DFT band (252/253), DOS/PDOS (116-120), Fermi surface (262), elastic from file (202), EOS fitting (205), optical (710/711), etc. Use when user has VASP result files (POSCAR, INCAR, EIGENVAL, DOSCAR, KPOINTS, KPATH.in, PROCAR, vasprun.xml as needed) and needs analysis or derived files. Call via Skill run_script with script_name='run_vaspkit.py' and script_args='--task <N>' (e.g. '--task 303'). Does NOT generate POTCAR; pseudopotential not required."
skill_type: operator
---

# VASPKIT Post-Processing Skill

Runs VASPKIT in command-line mode for post-processing VASP results. All operations are **post-processing only** (no POTCAR generation), so no pseudopotential path is required.

## Prerequisites

- **VASPKIT** must be installed and on PATH (e.g. after `source ~/.bashrc`).
- Run the script **in the directory** that contains the required input files (POSCAR, EIGENVAL, OUTCAR, etc., depending on task).

## Scripts

### run_vaspkit.py

Runs a single VASPKIT task in the current working directory.

* **Usage**: `python run_vaspkit.py --task <number> [--symprec 1E-5] [--timesym 1] [extra options]`
* **Required**: `--task`: VASPKIT task number (e.g. 303, 211, 262).
* **Optional**:
  * `--symprec`: Symmetry tolerance (default 1E-5). Used by 302, 303, 251, etc.
  * `--timesym`: Time-reversal symmetry 1=on, 0=off. Used by 302, 303, 251.
  * Any other options passed as-is to vaspkit (e.g. `--timesym 0`).

* **Examples**:
  * K-path for bulk: `run_vaspkit.py --task 303 --symprec 1E-5`
  * K-path for 2D: `run_vaspkit.py --task 302 --symprec 1E-5`
  * Extract band structure: `run_vaspkit.py --task 211`
  * Fermi surface (after VASP run): `run_vaspkit.py --task 262`
  * DOS: `run_vaspkit.py --task 116` (or 117, 118, 119, 120)

## Common tasks (post-processing, no POTCAR)

VASPKIT reads Fermi level from DOSCAR and calculation parameters from INCAR. Most post-processing tasks require both files in addition to the data files listed below.

| Task | Purpose | Required inputs |
|------|---------|-----------------|
| 302 | K-path for 2D/slab | POSCAR |
| 303 | K-path for bulk | POSCAR |
| 116 | PDOS by element | POSCAR, INCAR, DOSCAR (+ PROCAR for orbital projection) |
| 117 | Total DOS | POSCAR, INCAR, KPOINTS, EIGENVAL, DOSCAR |
| 211 | Band structure | POSCAR, INCAR, EIGENVAL, DOSCAR, KPOINTS (must be Line-Mode) |
| 252 | Hybrid-DFT band | POSCAR, INCAR, EIGENVAL, DOSCAR, KPOINTS, KPATH.in |
| 253 | Hybrid projected band | POSCAR, INCAR, EIGENVAL, DOSCAR, KPOINTS, KPATH.in, PROCAR |
| 262 | Fermi surface | POSCAR, INCAR, EIGENVAL, DOSCAR, KPOINTS |
| 202 | Elastic constants (read from file) | POSCAR, ELASTIC_TENSOR.in (or ELASTIC_TENSOR_2D.in) |
| 205 | EOS fitting (from file) | EOS.in (or VPKIT.in) |
| 710 | Optical (2D) | POSCAR, INCAR, vasprun.xml |
| 711 | Optical (3D/bulk) | POSCAR, INCAR, vasprun.xml |

## When to use

* "Generate K-path for my POSCAR" -> `run_vaspkit.py --task 303 --symprec 1E-5` (bulk) or 302 (2D).
* "Extract band structure from this VASP run" -> ensure POSCAR, INCAR, EIGENVAL, DOSCAR, and Line-Mode KPOINTS all exist, then `run_vaspkit.py --task 211`.
* "Get DOS/PDOS" -> ensure POSCAR, INCAR, DOSCAR exist, then `run_vaspkit.py --task 116` (or 117 for total DOS, which also needs KPOINTS and EIGENVAL).
* "Fermi surface" -> ensure POSCAR, INCAR, EIGENVAL, DOSCAR, KPOINTS exist, then `run_vaspkit.py --task 262`.
* "Hybrid band structure" -> ensure POSCAR, INCAR, EIGENVAL, DOSCAR, KPOINTS, KPATH.in exist, then `run_vaspkit.py --task 252`.

## Tool (via Skill)

- **run_script** with **script_name**: `run_vaspkit.py`; **script_args**: e.g. `--task 303 --symprec 1E-5` or `--task 211`.

## Rules

* Run in the directory that contains the required VASP input/output files.
* This skill does **not** generate POTCAR; only use for post-processing. If vaspkit reports POTCAR error, the user may have triggered a task that needs POTCAR (e.g. 103); suggest using a post-processing task instead.
* For task 303/302, common output files: PRIMCELL.vasp, KPATH.in, HIGH_SYMMETRY_POINTS. User can copy PRIMCELL.vasp to POSCAR and KPATH.in to KPOINTS for band calculation.
