---
name: mlips
description: "Machine-learning interatomic potentials (MLIPs): structure optimization, phonon, molecular dynamics, elastic constants, NEB. Default model is DPA; also supports MACE, SevenNet, MatterSim. Bohrium GPU submission."
skill_type: operator
---

# MLIPs Skill — Machine Learning Interatomic Potentials

Universal machine-learning interatomic potentials for atomistic simulations. All tasks run via ASE calculators on Bohrium GPU nodes.

## Bohrium Submission Config

| Item | Default Value |
|------|---------------|
| image | `registry.dp.tech/dptech/dpa-calculator:cd96ac21` |
| machine | `c16_m64_1 * NVIDIA 4090` |
| cmd | `python {script} {args} > log 2>&1` |

> The default image has DPA (deepmd-kit), ASE, phonopy, pymatgen pre-installed.
> For MACE / SevenNet / MatterSim models, verify the image has the required packages or use `Bohrium(action="list_images", keyword="lambench")` to find an image that includes them.

## Available Models

| Model | Family | Domain | Notes |
|-------|--------|--------|-------|
| **DPA3.1-3M** | DP | General inorganic | Default. 3M params, 16 layers |
| **DPA3.2-5M** | DP | General + charge/spin | Supports `--charge` and `--spin` fparam |
| **DPA2.4-7M** | DP | General inorganic | 7M params, 37-head |
| MACE-MP-0 | MACE | General inorganic | Foundation model |
| MACE-MPA-0 | MACE | General inorganic | Multi-dataset |
| SevenNet-0 | SevenNet | General inorganic | Graph neural network |
| 7net-mf-ompa | SevenNet | General (OMat+MPTrj) | Multi-fidelity |
| MatterSim-v1-5M | MatterSim | General inorganic | 5M params |

### Head Selection (DPA only)

The `--head` flag selects the DPA model's application domain:

| Head | Domain | Use for |
|------|--------|---------|
| `Omat24` | Inorganic crystals | Oxides, metals, ceramics **(default)** |
| `OMol25` | Organic molecules | Drug-like compounds, ligands |
| `OC22` | Catalysis interfaces | Surfaces, adsorbates |
| `Organic_Reactions` | Organic reactions | Transition states, reaction profiles |
| `ODAC23` | MOFs / DAC | Metal-organic frameworks |

### Charge / Spin (DPA3.2-5M only)

Pass `--charge` and `--spin` only when using DPA3.2-5M. Other models ignore these flags.

- `--charge`: total charge in e (0=neutral, -1=anion, +1=cation)
- `--spin`: spin multiplicity 2S+1 (1=singlet, 2=doublet, 3=triplet)

## Task Types

| Task | Script | Description |
|------|--------|-------------|
| optimize | `optimize_structure.py` | Geometry optimization (atoms ± cell) |
| phonon | `calculate_phonon.py` | Phonon band structure, DOS, thermal properties |
| md | `run_molecular_dynamics.py` | Multi-stage MD (NVT/NPT/NVE) |
| elastic | `calculate_elastic.py` | Elastic constants (needs relaxed structure) |
| neb | `run_neb.py` | NEB transition-state search |

## Script Usage

### Structure Optimization

```bash
python optimize_structure.py --structure input.cif --model DPA3.1-3M \
    [--head Omat24] [--relax-cell] [--fmax 0.01] [--steps 100]
```

Output: `{stem}_optimized.cif`, `{stem}_traj.traj`, `result.json`

### Phonon Calculation

```bash
python calculate_phonon.py --structure input.cif --model DPA3.1-3M \
    --temperatures 300 600 900 [--calc-tdos] [--calc-pdos] [--mesh 40]
```

Output: `phonon_band.png`, `phonon_band.yaml`, `result.json`

### Molecular Dynamics

```bash
# First create a stages.json file:
cat > stages.json << 'EOF'
[
  {"mode": "NVT", "temperature_K": 300, "runtime_ps": 5, "timestep_ps": 0.0005},
  {"mode": "NPT-aniso", "temperature_K": 300, "pressure": 0.0, "runtime_ps": 10}
]
EOF

python run_molecular_dynamics.py --structure input.cif --model DPA3.1-3M \
    --stages stages.json [--save-interval 100] [--seed 42]
```

MD modes: `NVT`, `NVT-Berendsen`, `NVT-Langevin`, `NPT-aniso`, `NPT-tri`, `NVE`

Output: `trajs/stage*_*.extxyz`, `final_structure.xyz`, `md_simulation.log`, `result.json`

### Elastic Constants

```bash
python calculate_elastic.py --structure relaxed.cif --model DPA3.1-3M \
    [--fmax 0.01] [--norm-strain -0.01 0.01 4] [--shear-strain -0.06 0.06 4]
```

**Important**: Input must be a fully relaxed structure (run optimize first with `--relax-cell`).

Output: `elastic_matrix.csv`, `result.json` (bulk/shear/Young's modulus in GPa)

### NEB Transition State

```bash
python run_neb.py --initial initial.cif --final final.cif \
    --model DPA3.1-3M [--images 5] [--fmax 0.05] [--steps 500]
```

**Important**: Both structures must be fully relaxed and have the same atoms in the same order.

Output: `neb_band.pdf`, `result.json` (forward/reverse barrier in eV)

## Physical Checks

- **Model choice**: Use DPA3.1-3M for general inorganic; DPA3.2-5M when charge/spin matters; MACE/SevenNet for cross-validation
- **Convergence**: `--fmax 0.01` for optimization; `--fmax 0.05` for NEB; tighter thresholds need more steps
- **Cell relaxation**: Use `--relax-cell` for equilibrium properties (elastic, phonon); omit for constrained optimization
- **MD timestep**: 0.5 fs (0.0005 ps) is safe for most systems; light elements (H) may need 0.2 fs
- **Phonon mesh**: Higher `--mesh` gives better DOS but longer computation; 40 is a good default

## Submission Workflow

1. Prepare structure file (CIF/POSCAR/XYZ)
2. Copy the relevant script(s) and `_calculator.py` to a working directory
3. For MD: create a `stages.json` file with stage definitions
4. Submit: `Bohrium(action="submit", input_dir="<dir>", image="registry.dp.tech/dptech/dpa-calculator:cd96ac21", cmd="python optimize_structure.py --structure input.cif --model DPA3.1-3M > log 2>&1", machine="c16_m64_1 * NVIDIA 4090")`
5. Poll: `Bohrium(action="poll", job_id=<id>)`
6. Read `result.json` and output files from `result_dir`
7. Analyze results, iterate if needed

## Output Files

All scripts produce a `result.json` with key numerical results. Additional outputs:

| Script | Key Outputs |
|--------|-------------|
| optimize | `*_optimized.cif` (structure), `*_traj.traj` (trajectory) |
| phonon | `phonon_band.png` (plot), `phonon_band.yaml` (data) |
| md | `trajs/*.extxyz` (trajectories), `final_structure.xyz`, `md_simulation.log` |
| elastic | `elastic_matrix.csv` (6×6 tensor) |
| neb | `neb_band.pdf` (energy profile) |
