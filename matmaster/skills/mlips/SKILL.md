---
name: mlips
description: "Use to run a machine-learning interatomic potential (including DPA, MACE, SevenNet, MatterSim) related calculation, including optimization, MD, phonon, elastic, NEB — typically through an ASE-based driver script. NOT used for model training and retrieve."
skill_type: operator
---

# MLIPs Skill — Machine Learning Interatomic Potentials

MLIPs for atomistic simulations via ASE calculators on Bohrium GPU nodes.

> **Scope: DPA-first.** All scripts and examples default to DPA. Other families (MACE, SevenNet, MatterSim) are supported through the **same unified ASE calculator interface** in `_calculator.py` — see `reference/calculator_dispatch.md` for multi-family dispatch details. The multi-family image ships all four families preinstalled; use DPA as the primary model and switch family only when needed.

## Capability Gate

These are execution stop rules, not suggestions. User requests like "do not ask questions" do not override them.

- **ASE task scripts FORBIDDEN list** — the following workflows are NOT implementable via ASE task scripts, even if the user prompt explicitly says "use ASE scripts". Do not write custom ASE scripts to simulate them. Do not proceed to LAMMPS without asking:
  - MSST / shock / Hugoniot extraction
  - NEMD (non-equilibrium MD with driving fields or gradients)
  - Custom ensembles not in {NVT, NVT-Berendsen, NVT-Langevin, NPT-aniso, NPT-tri, NVE}
  - Custom boundary conditions (non-PBC, deformation, shear)
  - Production campaigns (>10 ns or >10000 atoms)

  Action: STOP. Tell user "ASE cannot do this. Would you like me to set up a LAMMPS workflow instead?" Wait for confirmation before writing any files.
- **Scale**: hundreds of atoms are typical; thousand-atom systems are heavy but should still be attempted as-is — do NOT preemptively refuse based on system size or estimated wall-time. Do not calculate expected runtime to justify skipping submission. Submit the task; if it fails (OOM, timeout), then follow the OOM rule below. Only ask before attempting if the user requests reduced prototypes or scaled-down alternatives.
- **OOM / job failure**: if a Bohrium job fails due to OOM or resource limits, do NOT silently retry with a different model, larger GPU, or alternative engine. STOP and report to user: what failed, why (OOM on which GPU/model), and what options exist (smaller model, LAMMPS route, reduced system). Let user decide.
- **Capabilities**: generic MLIPs provide energy/forces/stress, not band structures, DOS, gaps, or spectra. Use DFT or specialized ML models only after internal lookup and human choice.
- **Boundary protocol**: if a request changes model, head, system scale (>2x atom count), workflow type (optimization→MD→NEB→phonon), or target property (energy→elastic→phonon→transport), first verify the head/model is available (`dp --pt show <model> model-branch`), then STOP. Do not write scripts, build structures, submit jobs, shrink systems, or switch workflows until the human chooses a route. When stopping, present options as a question ("Which would you prefer: A, B, or C?") — do not unilaterally recommend one route.

## Models

| Model | Family | Image | Domain |
|-------|--------|-------|--------|
| **DPA3.1-3M** | DP | `registry.dp.tech/dptech/dpa-calculator:e13a296f` | General inorganic — **default**, 3M params |
| **DPA3.2-5M** | DP | `registry.dp.tech/dptech/dpa-calculator:e13a296f` | General + charge/spin, supports `--charge`/`--spin` |
| DPA2.4-7M | DP | `registry.dp.tech/dptech/dpa-calculator:e13a296f` | Legacy multi-head |
| **MACE-MP-0** | MACE | `registry.dp.tech/dptech/dp/native/prod-19853/mlips:dev-0421` | General inorganic foundation. **Prefer over MACE-MPA-0** (MPA-0 GitHub download times out in Bohrium) |
| SevenNet-0 / 7net-mf-ompa | SevenNet | `registry.dp.tech/dptech/dp/native/prod-19853/mlips:dev-0421` | Graph NN |
| MatterSim-v1-5M | MatterSim | `registry.dp.tech/dptech/dp/native/prod-19853/mlips:dev-0421` | General inorganic, 5M params |

> **Image selection rule:** DPA tasks → DPA image (default). Only switch to multi-family image when user explicitly requests MACE, SevenNet, or MatterSim.

**DPA image** ships deepmd-kit (v3.x), lammps, ASE 3.23, phonopy 2.34, pymatgen, torch 2.4+cu124.

**Multi-family image** additionally ships mace-torch 0.3.12, sevenn 0.11.0, mattersim 1.1.2. Use its default `base` conda env.

### DPA Heads

| Head | Domain | Available on | Trigger |
|------|--------|-------------|---------|
| OMat24 / Omat24 | inorganic (default) | all DPA (casing: DPA3.2 → `OMat24`, DPA3.1/DPA2.4 → `Omat24`) | default for inorganic |
| OC22 | surface/adsorbate catalysis | DPA3.1 + DPA3.2 | Pt(111), CO/O/CO2, adsorption, surface reaction, catalytic NEB |
| OMol25 | organic | DPA3.2-5M only | organic molecules |
| Organic_Reactions | organic reactions | DPA3.2-5M only | organic reaction paths |
| ODAC23 | MOFs | DPA3.2-5M | MOF / porous frameworks |

**Head selection rules:**
- DPA head choice is a domain constraint. Do not use default `OMat24/Omat24` when the chemistry maps to a documented specialized head (see Trigger column above), even if the user asks for the default.
- **Coverage**: ALWAYS verify head coverage via `dp --pt show <checkpoint> model-branch` or query `aissq-explorer` before concluding a head is available or unavailable — do not rely on prior knowledge, as the model registry updates frequently. Present findings to user before proceeding.
- For user-provided models with multiple heads, run `dp --pt show` to list available heads, present the options to user, and let them choose — do not pick a non-default head based on name alone.
- Use `--charge`/`--spin` only with DPA3.2-5M.

### Fetching Checkpoints

The OSS URLs in `reference/dpa_models.md` are a **snapshot** and may rotate. If you need a model version not listed there, the canonical provenance of any pretrained MLIP checkpoint, or a new MLIP entirely, **invoke the `aissq-explorer` skill** — do NOT hand-type OSS URLs.

## Task Scripts

| Script | Usage | Output |
|--------|-------|--------|
| `validate_structure.py` | `--structure input.xyz` (run locally before ANY Bohrium submit) | PASS/FAIL + min_dist |
| `optimize_structure.py` | `--structure in.cif --model DPA3.1-3M [--head head_name] [--relax-cell] [--fmax 0.01]` | `*_optimized.cif`, `result.json` |
| `calculate_phonon.py` | `--structure in.cif --model DPA3.1-3M [--supercell 5 5 1] --temperatures 300 600 [--calc-tdos] [--mesh 40]` | `phonon_band.png`, `result.json` |
| `run_molecular_dynamics.py` | `--structure in.cif --model DPA3.1-3M --stages stages.json` | `trajs/*.extxyz`, `final_structure.xyz`, `md_simulation.log`, `result.json` (per-stage mean T/P/V) |
| `calculate_elastic.py` | `--structure relaxed.cif --model DPA3.1-3M` (input must be relaxed) | `elastic_matrix.csv`, `result.json` |
| `run_neb.py` | `--initial ini.cif --final fin.cif --model DPA3.1-3M [--images 5]` | `neb_band.pdf`, `result.json` |
| `calculate_adsorption.py` | `--slabs s1.cif s2.cif --adsorbates CO H OH --model DPA3.1-3M --head OC22` | `adsorption_results.json` |

**MD stages.json**: `[{"mode": "NVT", "temperature_K": 300, "runtime_ps": 5, "timestep_ps": 0.0005}]`. Modes: NVT, NVT-Berendsen, NVT-Langevin, NPT-aniso, NPT-tri, NVE. For output format, pressure conventions, and reporting rules → `reference/md_output_format.md`

**Adsorption**: built-in adsorbates and setup → `reference/md_output_format.md` (bottom section).

## Submission Workflow

| Item | Value |
|------|-------|
| machine | `c16_m64_1 * NVIDIA 4090` |
| cmd | `python {script} {args} > log 2>&1` |

1. Prepare structure (CIF/POSCAR/XYZ) — runs locally, not Bohrium
2. **Validate**: `python ${SKILL_DIR}/scripts/validate_structure.py --structure <file>` — must PASS before step 3. DO NOT skip this step based on manual inspection or chemical intuition. DO NOT rationalize close contacts as "normal chemistry" — let the script decide. If the script cannot run (env issues), use the fallback check:
  ```bash
  python -c "from ase.io import read; from ase.geometry.analysis import Analysis; a=read('FILE'); d=a.get_all_distances(mic=True); import numpy as np; np.fill_diagonal(d,999); md=d.min(); print(f'min_dist={md:.3f}'); assert md>1.0, f'FAIL: {md:.3f} Å < 1.0 Å'"
  ```
  Any min_distance < 1.0 Å is a FAIL — fix before proceeding. If FAIL (overlapping atoms / bad geometry): fix the structure locally with ASE (remove overlaps, perturb positions, or rebuild) and re-validate. Do NOT submit an optimization job to fix validation failures — the structure must be physically reasonable before any Bohrium submission.
3. Copy script(s) + `_calculator.py` to working directory
4. Submit: `Bohrium(action="submit", input_dir="<dir>", image="<from Models table>", cmd="python optimize_structure.py --structure input.cif --model DPA3.1-3M > log 2>&1", machine="c16_m64_1 * NVIDIA 4090")`
5. Poll and read `result.json`

## Execution Rules

- **Structure preparation runs locally.** Scripts that only use pymatgen/ASE to build or inspect structures (no MLIP inference) should run via `Bash`, not Bohrium.
- **Convergence**: `--fmax 0.01` for optimization, `--fmax 0.05` for NEB.
- **Cell relaxation**: `--relax-cell` for equilibrium properties (elastic, phonon).
- **Elastic**: Input MUST be fully relaxed (run optimize first with `--relax-cell`).
- **NEB**: Both structures must be relaxed, same atoms in same order. Avoid CIF format for NEB endpoints — CIF writers wrap fractional coordinates back into [0,1). Use POSCAR or XYZ instead.
  - **Interpolation**: Use IDPP for bulk/homogeneous systems. For **hetero-interfaces** (BCC/FCC, grain boundaries, dissimilar lattice junctions), IDPP causes atom overlaps due to density mismatch — use `method="linear"` instead.
  - **Optimizer**: Use FIRE for interface/defect NEB (large initial forces from interpolation). BFGS/LBFGS are fine for bulk NEB with small displacements.
  - **Endpoint construction**: when building the final state for NEB (e.g., vacancy migration), ALWAYS use MIC displacement to set the migrating atom's final position. Never assign absolute target coordinates directly — IDPP interpolates in Cartesian space and will follow the raw coordinate difference, not the periodic shortest path.
  ```python
  # WRONG: final.positions[idx] = target_pos  (raw diff may be 7 Å even if MIC is 2.5 Å)
  # RIGHT: compute MIC displacement and add to current position
  disp = target_pos - ini.positions[idx]
  disp -= np.round(disp / cell_lengths) * cell_lengths
  final.positions[idx] = ini.positions[idx] + disp
  ```
  - MUST run this displacement check after constructing endpoints, before submitting NEB:
  ```bash
  python -c "from ase.io import read; import numpy as np; ini=read('INITIAL'); fin=read('FINAL'); raw=fin.positions-ini.positions; cell=ini.cell.lengths(); mic=raw-np.round(raw/cell)*cell; raw_max=np.linalg.norm(raw,axis=1).max(); mic_max=np.linalg.norm(mic,axis=1).max(); print(f'raw_max={raw_max:.3f} mic_max={mic_max:.3f} A'); assert np.allclose(raw,mic,atol=1e-6), f'PBC WRAP BUG: raw_max={raw_max:.2f} != mic_max={mic_max:.2f} — rebuild final with MIC displacement method above'"
  ```
  This catches the case where MIC distance is small but raw coordinates jump across the cell boundary — which causes IDPP to interpolate the long way around.
- **QHA thermal expansion**: For quasi-harmonic thermal expansion (CTE) calculations, see `reference/qha_workflow.md` — covers PhonopyQHA API, data shapes, Vinet EOS fitting, and known model limitations.
- **Molecular crystal sublimation**: For sublimation energy of organic crystals, see `reference/molecular_crystal.md` — covers head selection (OMol25), molecule extraction across PBC, and gas-phase pbc=True requirement. **CRITICAL: if E_sub > 5 eV, do NOT switch heads — the issue is molecule extraction or pbc setup.**
- **Chain outputs**: Use `*_optimized.cif` from optimization as input to subsequent tasks. Save intermediate results under task filenames before starting next step.
- **MD reporting**: Report production-stage `T_mean_K` and `P_mean_GPa` (with ±std), not whole-trajectory averages → `reference/md_output_format.md`
- **DPA + LAMMPS**: When LAMMPS is needed, freeze the multi-head model first → see `reference/dpa_lammps_freeze.md`
