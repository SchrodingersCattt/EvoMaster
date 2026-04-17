---
name: mlips
description: "Machine-learning interatomic potentials (MLIPs): structure optimization, phonon, molecular dynamics, elastic constants, NEB, adsorption energy. Default model is DPA; also supports MACE, SevenNet, MatterSim. Bohrium GPU submission."
skill_type: operator
---

# MLIPs Skill — Machine Learning Interatomic Potentials

Universal MLIPs for atomistic simulations via ASE calculators on Bohrium GPU nodes.

## Bohrium Submission

| Item | Default |
|------|---------|
| image | `registry.dp.tech/dptech/dpa-calculator:e13a296f` |
| machine | `c16_m64_1 * NVIDIA 4090` |
| cmd | `python {script} {args} > log 2>&1` |

> Image has DPA, ASE, phonopy, pymatgen pre-installed. Prepend `pip install ... &&` for missing packages. For MACE/SevenNet/MatterSim, check `Bohrium(action="list_images", keyword="lambench")`.

## Models

| Model | Domain | Notes |
|-------|--------|-------|
| **DPA3.1-3M** | General inorganic | Default. 3M params |
| **DPA3.2-5M** | General + charge/spin | Supports `--charge` and `--spin` |
| MACE-MP-0 / MACE-MPA-0 | General inorganic | Foundation models |
| SevenNet-0 / 7net-mf-ompa | General inorganic | Graph NN |
| MatterSim-v1-5M | General inorganic | 5M params |

**DPA heads**: `Omat24` (default, inorganic), `OMol25` (organic), `OC22` (catalysis), `Organic_Reactions`, `ODAC23` (MOFs). Use `--charge`/`--spin` only with DPA3.2-5M.

## Task Scripts

| Script | Usage | Output |
|--------|-------|--------|
| `optimize_structure.py` | `--structure in.cif --model DPA3.1-3M [--head Omat24] [--relax-cell] [--fmax 0.01]` | `*_optimized.cif`, `result.json` |
| `calculate_phonon.py` | `--structure in.cif --model DPA3.1-3M --temperatures 300 600 [--calc-tdos] [--mesh 40]` | `phonon_band.png`, `result.json` |
| `run_molecular_dynamics.py` | `--structure in.cif --model DPA3.1-3M --stages stages.json` | `trajs/*.extxyz`, `final_structure.xyz`, `result.json` |
| `calculate_elastic.py` | `--structure relaxed.cif --model DPA3.1-3M` (input must be relaxed) | `elastic_matrix.csv`, `result.json` |
| `run_neb.py` | `--initial ini.cif --final fin.cif --model DPA3.1-3M [--images 5]` | `neb_band.pdf`, `result.json` |
| `calculate_adsorption.py` | `--slabs s1.cif s2.cif --adsorbates CO H OH --model DPA3.1-3M --head OC22` | `adsorption_results.json` |

**MD stages.json**: `[{"mode": "NVT", "temperature_K": 300, "runtime_ps": 5, "timestep_ps": 0.0005}]`. Modes: NVT, NVT-Berendsen, NVT-Langevin, NPT-aniso, NPT-tri, NVE.

**Adsorption built-in adsorbates**: H, C, O, N, CO, CO2, H2, H2O, OH, OOH, COOH, HCOO, CHO. Copy both `_calculator.py` and `calculate_adsorption.py` to working directory.

## Key Rules

- **Convergence**: `--fmax 0.01` for optimization, `--fmax 0.05` for NEB.
- **Cell relaxation**: `--relax-cell` for equilibrium properties (elastic, phonon).
- **Elastic**: Input MUST be fully relaxed (run optimize first with `--relax-cell`).
- **NEB**: Both structures must be relaxed, same atoms in same order.
- **Chain outputs**: Use `*_optimized.cif` from optimization as input to subsequent tasks. **Save intermediate results** under task filenames before starting next step.

## Submission Workflow

1. Prepare structure (CIF/POSCAR/XYZ)
2. Copy script(s) + `_calculator.py` to working directory
3. Submit: `Bohrium(action="submit", input_dir="<dir>", image="registry.dp.tech/dptech/dpa-calculator:e13a296f", cmd="python optimize_structure.py --structure input.cif --model DPA3.1-3M > log 2>&1", machine="c16_m64_1 * NVIDIA 4090")`
4. Poll and read `result.json`
