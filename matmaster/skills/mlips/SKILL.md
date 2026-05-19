---
name: mlips
description: "MUST use this skill for ANY task involving MLIPs (machine-learning interatomic potentials such as DPA, MACE, SevenNet, MatterSim) — structure optimization, phonon, molecular dynamics, elastic constants, NEB, adsorption energy, etc."
skill_type: operator
---

# MLIPs Skill — Machine Learning Interatomic Potentials

MLIPs for atomistic simulations via ASE calculators on Bohrium GPU nodes.

> **Scope: DPA-first.** All scripts and examples default to DPA. Other families (MACE, SevenNet, MatterSim) are supported through the **same unified ASE calculator interface** in `_calculator.py`. The multi-family image ships all four families preinstalled; use DPA as the primary model and switch family only when needed.

## Capability Gate

These are execution stop rules, not suggestions. User requests like "do not ask questions" do not override them.

- **ASE task scripts FORBIDDEN list** — the following workflows are NOT implementable via ASE task scripts. Do not write custom ASE scripts to simulate them. Do not proceed to LAMMPS without asking:
  - MSST / shock / Hugoniot extraction
  - NEMD (non-equilibrium MD with driving fields or gradients)
  - Custom ensembles not in {NVT, NVT-Berendsen, NVT-Langevin, NPT-aniso, NPT-tri, NVE}
  - Custom boundary conditions (non-PBC, deformation, shear)
  - Production campaigns (>10 ns or >10000 atoms)

  Action: STOP. Tell user "ASE cannot do this. Would you like me to set up a LAMMPS workflow instead?" Wait for confirmation before writing any files.
- **Scale**: hundreds of atoms are typical; thousand-atom systems are heavy but should still be attempted as-is — do NOT preemptively refuse or ask for confirmation based on system size alone. If the job fails due to OOM, then follow the OOM rule below. Only ask before attempting if the user requests reduced prototypes or scaled-down alternatives.
- **OOM / job failure**: if a Bohrium job fails due to OOM or resource limits, do NOT silently retry with a different model, larger GPU, or alternative engine. STOP and report to user: what failed, why (OOM on which GPU/model), and what options exist (smaller model, LAMMPS route, reduced system). Let user decide.
- **Capabilities**: generic MLIPs provide energy/forces/stress, not band structures, DOS, gaps, or spectra. Use DFT or specialized ML models only after internal lookup and human choice.
- **Boundary protocol**: if a request changes model/head/scale/workflow/property class, first verify the head/model is available (`dp --pt show <model> model-branch`), then STOP. Do not write scripts, build structures, submit jobs, shrink systems, or switch workflows until the human chooses a route. When stopping, present options as a question ("Which would you prefer: A, B, or C?") — do not unilaterally recommend one route.

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
| `run_molecular_dynamics.py` | `--structure in.cif --model DPA3.1-3M --stages stages.json` | `trajs/*.extxyz`, `final_structure.xyz`, `result.json` |
| `calculate_elastic.py` | `--structure relaxed.cif --model DPA3.1-3M` (input must be relaxed) | `elastic_matrix.csv`, `result.json` |
| `run_neb.py` | `--initial ini.cif --final fin.cif --model DPA3.1-3M [--images 5]` | `neb_band.pdf`, `result.json` |
| `calculate_adsorption.py` | `--slabs s1.cif s2.cif --adsorbates CO H OH --model DPA3.1-3M --head OC22` | `adsorption_results.json` |

**MD stages.json**: `[{"mode": "NVT", "temperature_K": 300, "runtime_ps": 5, "timestep_ps": 0.0005}]`. Modes: NVT, NVT-Berendsen, NVT-Langevin, NPT-aniso, NPT-tri, NVE.

**Adsorption built-in adsorbates**: H, C, O, N, CO, CO2, H2, H2O, OH, OOH, COOH, HCOO, CHO. Copy both `_calculator.py` and `calculate_adsorption.py` to working directory.

## Submission Workflow

| Item | Value |
|------|-------|
| machine | `c16_m64_1 * NVIDIA 4090` |
| cmd | `python {script} {args} > log 2>&1` |

1. Prepare structure (CIF/POSCAR/XYZ) — runs locally, not Bohrium
2. **Validate**: `python ${SKILL_DIR}/scripts/validate_structure.py --structure <file>` — must PASS before step 3
3. Copy script(s) + `_calculator.py` to working directory
4. Submit: `Bohrium(action="submit", input_dir="<dir>", image="<from Models table>", cmd="python optimize_structure.py --structure input.cif --model DPA3.1-3M > log 2>&1", machine="c16_m64_1 * NVIDIA 4090")`
5. Poll and read `result.json`

## Execution Rules

- **Structure preparation runs locally.** Scripts that only use pymatgen/ASE to build or inspect structures (no MLIP inference) should run via `Bash`, not Bohrium.
- **Convergence**: `--fmax 0.01` for optimization, `--fmax 0.05` for NEB.
- **Cell relaxation**: `--relax-cell` for equilibrium properties (elastic, phonon).
- **Elastic**: Input MUST be fully relaxed (run optimize first with `--relax-cell`).
- **NEB**: Both structures must be relaxed, same atoms in same order. Avoid CIF format for NEB endpoints — CIF writers wrap fractional coordinates back into [0,1). Use POSCAR or XYZ instead. MUST run this MIC check after constructing endpoints, before submitting NEB:
  ```bash
  python -c "from ase.io import read; import numpy as np; ini=read('INITIAL'); fin=read('FINAL'); diff=fin.positions-ini.positions; cell=ini.cell.lengths(); diff-=np.round(diff/cell)*cell; md=np.linalg.norm(diff,axis=1).max(); print(f'max_disp={md:.3f} A'); assert md<cell.min()/2, f'MIC FAIL: {md:.2f} A — fix endpoint coords'"
  ```
  If it fails, the migrating atom's coordinates cross a cell boundary — shift by one lattice vector so the straight-line path is the shortest.
- **Chain outputs**: Use `*_optimized.cif` from optimization as input to subsequent tasks. Save intermediate results under task filenames before starting next step.
- **DPA + LAMMPS**: When LAMMPS is needed, freeze the multi-head model first → see `reference/dpa_lammps_freeze.md`
