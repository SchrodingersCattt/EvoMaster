---
name: abacus
description: "Use to RUN ABACUS calculations: SCF, relax/cell-relax, band/DOS, MD, surfaces, defects, DFT+U, phonon, EOS. Covers input prep, Bohrium submit, parsing, parameter guards, K-points, validation. Do NOT use for ABACUS literature search, generic DFT theory, or input-only handoff."
skill_type: operator
---

# ABACUS Skill

PW and LCAO basis; this skill produces correct, runnable files and avoids silent-failure configurations.

## Workflow

1. **Read provided STRU** — reuse PP/orbital filenames exactly as given.
2. **Read `references/input_examples.md`** — mandatory parameter tables, task-type examples, common mistakes. This is your primary reference for writing correct INPUT.
3. **Resolve PP/orbital names** for Bohrium: look up `references/apns_pseudopotentials_v1.list` and `references/apns_orbitals_efficiency_v1.list`. If any element cannot be resolved, STOP and report.
4. **Write INPUT + KPT** following the examples and mandatory-parameter tables from step 2.
5. **Run validator**: `python ${SKILL_DIR}/scripts/validate_input.py --dir <dir>`. Fix all FAIL items.
6. **Submit to Bohrium** (if task requires execution).

## Hard Guards — Silent Failures

These cause wrong results or crashes with NO error message. Always check:

| Rule | Why |
|------|-----|
| `pseudo_dir` in INPUT; `orbital_dir` for LCAO | ABACUS can't find PP/orb files |
| `ntype` = species count in STRU `ATOMIC_SPECIES` | Mismatch → crash or wrong atom assignment |
| `cal_force 1` for relax/cell-relax/md | No forces → optimizer silently does nothing |
| `cal_stress 1` for cell-relax | Cell vectors never optimized |
| SCF→NSCF: SCF must have `out_chg 1` | NSCF can't read charge density |
| SCF→NSCF: NSCF must have `init_chg file` + `symmetry 0` | Without these: re-does SCF / folds k-path |
| Non-default filenames: set `stru_file` + `kpoint_file` in INPUT | ABACUS looks for `STRU`/`KPT` by default |
| ASE Calculator mode: use `calculation scf` only | `relax`/`md` conflicts with ASE's external optimizer |

## Bohrium Submission

| Item | Default |
|------|---------|
| image | `registry.dp.tech/dptech/dp/native/hub/mrdic2/abacusp:1.0.3-1778742780` |
| machine | `c32_m128_cpu` |
| cmd | `OMP_NUM_THREADS=1 mpirun -np 16 abacus > log 2>&1` |

PP path: `/root/apns-pseudopotentials-v1/`. Orbital path: `/root/apns-orbitals-efficiency-v1/`.

## Reference Routing

Read the relevant reference **before writing files** when your task matches:

| Task involves | Read |
|---------------|------|
| Any INPUT generation | `references/input_examples.md` (always — step 2 above) |
| Multi-element system (>=3 species) | `references/stru_multispecies.md` |
| Writing or editing STRU | `references/stru_format.md` |
| Bader charge / wavefunction output / get_wf | `references/advanced_tasks.md` |
| Surface/slab with dipole or E-field or gate | `references/electric_field.md` |
| DFT+U / phonon / EOS / surface energy | `references/advanced_tasks.md` |
| Extracting results from ABACUS output | `references/output_params.md` |
| Troubleshooting failures | `references/troubleshooting.md` |

PP/orbital filename lookup:
- `references/apns_pseudopotentials_v1.list`
- `references/apns_orbitals_efficiency_v1.list`
