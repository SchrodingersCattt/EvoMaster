---
name: mlips
description: "MUST use this skill for ANY task involving MLIPs (machine-learning interatomic potentials such as DPA, MACE, SevenNet, MatterSim) — structure optimization, phonon, molecular dynamics, elastic constants, NEB, adsorption energy, etc."
skill_type: operator
---

# MLIPs Skill — Machine Learning Interatomic Potentials

MLIPs for atomistic simulations via ASE calculators on Bohrium GPU nodes.

> **Scope: DPA-first.** All scripts and examples default to DPA. Other families (MACE, SevenNet, MatterSim) are supported through the **same unified ASE calculator interface** in `_calculator.py`. The multi-family image ships all four families preinstalled; use DPA as the primary model and switch family only when needed.

## Bohrium Submission

> **Image selection rule — IMPORTANT:**
> - **DPA tasks → always use the DPA image** (default, covers all DPA models).
> - **Only switch to the multi-family image when the user explicitly requests MACE, SevenNet, or MatterSim.**

| Item | Value |
|------|-------|
| **image (DPA, default)** | `registry.dp.tech/dptech/dpa-calculator:e13a296f` |
| image (multi-family, only for MACE/SevenNet/MatterSim) | `registry.dp.tech/dptech/dp/native/prod-19853/mlips:dev-0421` |
| machine | `c16_m64_1 * NVIDIA 4090` |
| cmd | `python {script} {args} > log 2>&1` |

**DPA image** ships deepmd-kit (v3.x), lammps, ASE 3.23, phonopy 2.34, pymatgen, torch 2.4+cu124. Supports all DPA models and all task scripts out-of-the-box.

**Multi-family image** additionally ships mace-torch 0.3.12, sevenn 0.11.0, mattersim 1.1.2. Use its default `base` conda env; ignore `fc`/`test` envs (incomplete subsets). deepmd-kit reports `1.3.3.dev2445` via git-describe — this **is** the v3.0.0+ codebase.

## Models

| Model | Family | Image | Domain |
|-------|--------|-------|--------|
| **DPA3.1-3M** | DP | **DPA (default)** | General inorganic — **default**, 3M params |
| **DPA3.2-5M** | DP | **DPA (default)** | General + charge/spin, supports `--charge`/`--spin` |
| DPA2.4-7M | DP | **DPA (default)** | Legacy multi-head |
| **MACE-MP-0** | MACE | multi-family | General inorganic foundation. **Prefer over MACE-MPA-0** (MPA-0 GitHub download times out in Bohrium) |
| SevenNet-0 / 7net-mf-ompa | SevenNet | multi-family | Graph NN |
| MatterSim-v1-5M | MatterSim | multi-family | General inorganic, 5M params |

> Non-DPA families require the multi-family image. Do NOT use the multi-family image for pure DPA tasks.

**DPA heads**: `OMat24` or `Omat24` (default, inorganic — casing differs between model versions: DPA3.2-5M uses `OMat24`, DPA3.1-3M/DPA2.4-7M use `Omat24`), `OMol25` (organic), `OC22` (surface/adsorbate catalysis — required first check for Pt(111), CO/O/CO2, adsorption, surface reaction, and catalytic NEB), `Organic_Reactions`, `ODAC23` (MOFs). Use `--charge`/`--spin` only with DPA3.2-5M. Not all heads are available on all models — if a head is missing from the checkpoint, switch model version (e.g., `OMol25` requires DPA3.2-5M; it is not available on DPA3.1-3M). Run `dp --pt show <checkpoint> model-branch` to verify before submitting.

## Decision Boundaries

These are execution stop rules, not suggestions. User requests like "do not ask questions" do not override them.

- **Boundary protocol**: if a request changes model/head/scale/workflow/property class, first do the internal check, then STOP. Do not write scripts, build structures, submit jobs, shrink systems, or switch workflows until the human chooses a route. When stopping, present options as a question ("Which would you prefer: A, B, or C?") — do not unilaterally recommend one route.
- **Coverage**: the default head is not universal. ALWAYS verify head coverage via `dp --pt show <checkpoint> model-branch` or query `aissq-explorer` before concluding a head is available or unavailable — do not rely on prior knowledge, as the model registry updates frequently. Present findings to user before proceeding.
- **DPA head validity**: DPA head choice is a domain constraint. Do not use default `OMat24/Omat24` when the chemistry maps to a documented specialized head (`OC22`, `Organic_Reactions`, `OMol25`, `ODAC23`), even if the user asks for the default. For surfaces/adsorbate catalysis, check/use `OC22`, not `OMat24/Omat24`. Check/select the domain head first, then ask before changing the requested setup. For user-provided models with multiple heads, run `dp --pt show` to list available heads, present the options to user, and let them choose — do not pick a non-default head based on name alone.
- **Scale**: hundreds of atoms are typical; thousand-atom systems are heavy but should still be attempted as-is — do NOT preemptively refuse or ask for confirmation based on system size alone. If the job fails due to OOM, then follow the OOM rule below. Only ask before attempting if the user requests reduced prototypes or scaled-down alternatives.
- **OOM / job failure**: if a Bohrium job fails due to OOM or resource limits, do NOT silently retry with a different model, larger GPU, or alternative engine. STOP and report to user: what failed, why (OOM on which GPU/model), and what options exist (smaller model, LAMMPS route, reduced system). Let user decide.
- **ASE task scripts FORBIDDEN list** — the following workflows are NOT implementable via ASE task scripts. Do not write custom ASE scripts to simulate them. Do not proceed to LAMMPS without asking:
  - MSST / shock / Hugoniot extraction
  - NEMD (non-equilibrium MD with driving fields or gradients)
  - Custom ensembles not in {NVT, NVT-Berendsen, NVT-Langevin, NPT-aniso, NPT-tri, NVE}
  - Custom boundary conditions (non-PBC, deformation, shear)
  - Production campaigns (>10 ns or >10000 atoms)

  Action: STOP. Tell user "ASE cannot do this. Would you like me to set up a LAMMPS workflow instead?" Wait for confirmation before writing any files.
- **Capabilities**: generic MLIPs provide energy/forces/stress, not band structures, DOS, gaps, or spectra. Use DFT or specialized ML models only after internal lookup and human choice.

## Fetching checkpoints

The OSS URLs in `reference/dpa_models.md` are a **snapshot** and may rotate. If you need a model version not listed there, the canonical provenance (file name, byte size, download URL, modify date) of any pretrained MLIP checkpoint, or a new MLIP entirely, **invoke the `aissq-explorer` skill** (`backend.aissquare.com` public registry) — do NOT hand-type OSS URLs. The downloaded `.pt`/`.pth`/`.model` file is then used here exactly the same way.

## Conditional Routing — check BEFORE acting

| When your task involves… | You MUST run… |
|--------------------------|---------------|
| Submitting ANY Bohrium job | Pre-Submission Validation above (min_dist check) |
| NEB calculation | MIC displacement check (see Key Rules § NEB) |
| User-provided model with multiple heads | `dp --pt show` → present head options to user |
| Workflow not in {optimize, phonon, MD, elastic, NEB, adsorption} | Check FORBIDDEN list above |
| Choosing a DPA head for unfamiliar chemistry | `dp --pt show` or `aissq-explorer` query |

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

## Pre-Submission Validation (MANDATORY)

Before submitting ANY job to Bohrium (optimization, MD, phonon, NEB, elastic — no exceptions), run:

```bash
python ${SKILL_DIR}/scripts/validate_structure.py --structure <file>
```

If it reports FAIL, fix the structure (energy minimization or rebuild) before proceeding. Skipping this step will crash the simulation.

## Key Rules

- **Structure preparation runs locally.** Scripts that only use pymatgen/ASE to build or inspect structures (no MLIP inference) should run via `Bash`, not Bohrium. Only submit to Bohrium when the script imports `_calculator.py` or calls a model. This avoids wasting minutes on submit/poll/download cycles for pure-Python tasks.
- **Convergence**: `--fmax 0.01` for optimization, `--fmax 0.05` for NEB.
- **Cell relaxation**: `--relax-cell` for equilibrium properties (elastic, phonon).
- **Elastic**: Input MUST be fully relaxed (run optimize first with `--relax-cell`).
- **NEB**: Both structures must be relaxed, same atoms in same order. Avoid CIF format for NEB endpoints — CIF writers wrap fractional coordinates back into [0,1). Use POSCAR or XYZ instead. MUST run this MIC check after constructing endpoints, before submitting NEB:
  ```bash
  python -c "from ase.io import read; import numpy as np; ini=read('INITIAL'); fin=read('FINAL'); diff=fin.positions-ini.positions; cell=ini.cell.lengths(); diff-=np.round(diff/cell)*cell; md=np.linalg.norm(diff,axis=1).max(); print(f'max_disp={md:.3f} A'); assert md<cell.min()/2, f'MIC FAIL: {md:.2f} A — fix endpoint coords'"
  ```
  If it fails, the migrating atom's coordinates cross a cell boundary — shift by one lattice vector so the straight-line path is the shortest.
- **Chain outputs**: Use `*_optimized.cif` from optimization as input to subsequent tasks. **Save intermediate results** under task filenames before starting next step.

## Submission Workflow

1. Prepare structure (CIF/POSCAR/XYZ)
2. **Validate**: `python ${SKILL_DIR}/scripts/validate_structure.py --structure <file>` — must PASS before step 3
3. Copy script(s) + `_calculator.py` to working directory
4. Submit (DPA — default): `Bohrium(action="submit", input_dir="<dir>", image="registry.dp.tech/dptech/dpa-calculator:e13a296f", cmd="python optimize_structure.py --structure input.cif --model DPA3.1-3M > log 2>&1", machine="c16_m64_1 * NVIDIA 4090")`
   Submit (MACE/SevenNet/MatterSim only): replace image with `registry.dp.tech/dptech/dp/native/prod-19853/mlips:dev-0421`
5. Poll and read `result.json`

## DPA + LAMMPS (requires freeze)

DPA3 checkpoints (`DPA3.1-3M.pt`, `DPA3.2-5M.pt`) are **multi-task / multi-head** models and **cannot be loaded directly by LAMMPS**. You must first freeze a single head/branch into a `.pth` file. This is DPA-specific (MACE/SevenNet/MatterSim do not need this step).

> Requires `deepmd-kit >= 3.1.0` (check with `dp --version`; the multi-family image reports `v1.3.3.dev2445` which **is** the v3.x codebase).

**Step 1 — list available branches (heads):**

```bash
dp --pt show DPA-3.2-5M.pt model-branch
```

Typical DPA3 branches: `OMat24` (default inorganic), `OMol25`, `OC22`, `Organic_Reactions`, `ODAC23`, plus `RANDOM` (randomly initialized fitting net). Pick the branch whose training data best matches your system.

**Step 2 — freeze the chosen branch:**

```bash
# --model-branch (preferred) or --head both work
dp --pt freeze -c DPA-3.2-5M.pt -o frozen_model.pth --model-branch [head_name]
```

**Step 3 — use the frozen `.pth` in LAMMPS** (via the `deepmd` pair style):

```
pair_style  deepmd frozen_model.pth
pair_coeff  * *
```

**Type-map alignment:** The frozen model keeps the full-element type_map by default. LAMMPS data file atom types must use the same element indices (e.g., Fe=26, Ni=28 — not compact 1,2). See `reference/dpa_models.md` § "Use in LAMMPS" for full details and the compact `--type-map` alternative.

Notes:
- The frozen `.pth` is also directly usable by ASE: `from deepmd.calculator import DP; atoms.calc = DP("frozen_model.pth")`.
- The ASE workflows provided by this skill (optimize/phonon/MD/elastic/NEB/adsorption) load the **multi-head `.pt`** directly and select the head via `--head`, so freezing is only required when you actually need LAMMPS.
- For a new downstream system, optionally run `dp --pt change-bias <model.pt> -s <system> --model-branch <Branch>` before freezing to better align the per-element energy bias.
