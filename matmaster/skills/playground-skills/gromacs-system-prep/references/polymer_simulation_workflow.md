# Polymer Simulation End-to-End Workflow

This reference connects all polymer-related skills into a cohesive end-to-end workflow.

## Skill Chain

```
poly-generator → poly-forcefield → gromacs-system-prep → gromacs (Bohrium) → md-analysis
```

## Step 1: Build Polymer Structure (poly-generator skill)

Use `build_polymer.py` to generate polymer SMILES and sequence.

```bash
# Homopolymer (e.g., 10 repeats of monomer A)
uv run python matmaster/skills/playground-skills/poly-generator/scripts/build_polymer.py \
  --monomers '{"A": "[*]CC[*]"}' --sequence A10 --output-json polymer.json

# Block copolymer
uv run python matmaster/skills/playground-skills/poly-generator/scripts/build_polymer.py \
  --monomers '{"A": "[*]CC[*]", "B": "[*]CC(C)[*]"}' --sequence A5B5 --output-json polymer.json
```

Then generate 3D structure:
```bash
uv run python matmaster/skills/playground-skills/poly-generator/scripts/generate_3d.py \
  --smiles "<polymer_smiles_from_step1>" --output polymer.pdb --format pdb
```

**Key flags**: `--monomers` (JSON object or file), `--sequence` (e.g. `A10`, `A5B5`), `--mode` (linear/block/random), `--output-json` (JSON output path).

## Step 2: Generate Force Field Topology (poly-forcefield skill)

Use `generate_gmx_top.py` to create a GROMACS `.top` file.

```bash
uv run python matmaster/skills/playground-skills/poly-forcefield/scripts/generate_gmx_top.py \
  --smiles "<polymer_smiles>" --output polymer.top --molecule-name POLY
```

**Current scope**: Linear alkane-like SMILES. For unsupported chemistries, state the limitation and consider alternatives (AMBER/GAFF via AmberTools, or literature force fields).

**Alternative for complex polymers**: If `generate_gmx_top.py` doesn't support the chemistry:
1. Use `acpype` or `antechamber` (AmberTools) for GAFF parameterization
2. Convert AMBER topology to GROMACS format
3. Or use pre-built force field files from literature

## Step 3: Prepare Simulation System (gromacs-system-prep skill)

Use `prepare_gmx.py` for box setup, solvation, and ion addition.

```bash
# Set box (ensure adequate box size for polymer)
uv run python matmaster/skills/playground-skills/gromacs-system-prep/scripts/prepare_gmx.py \
  editconf --input polymer.pdb --output box.gro --box 8.0 8.0 8.0

# Solvate
uv run python matmaster/skills/playground-skills/gromacs-system-prep/scripts/prepare_gmx.py \
  solvate --input box.gro --topology polymer.top --output solvated.gro

# Add ions (if needed)
uv run python matmaster/skills/playground-skills/gromacs-system-prep/scripts/prepare_gmx.py \
  genion --input ions.tpr --topology polymer.top --output ionized.gro --stdin-lines "SOL"
```

### Multi-Component Systems (Adhesion / Bilayer)

For polymer adhesion or multi-layer systems:
1. Build each polymer separately (Steps 1-2 for each component)
2. Use `editconf` to set box with separation gap
3. Stack components: `insert-molecules` or coordinate concatenation (adjust z-offsets)
4. Solvate and add ions
5. **Save each intermediate file** — partial results still have value

## Step 4: Generate MDP Files and Submit (gromacs skill)

### Standard MD Protocol: EM → NVT → NPT → Production

**Energy Minimization (em.mdp)**:
```
integrator = steep
emtol = 1000.0
nsteps = 50000
nstcgsteep = 10
```

**NVT Equilibration (nvt.mdp)**:
```
integrator = md
dt = 0.002
nsteps = 50000    ; 100 ps
tcoupl = V-rescale
ref_t = 300
tau_t = 0.1
constraints = h-bonds
```

**NPT Equilibration (npt.mdp)**:
```
integrator = md
dt = 0.002
nsteps = 50000    ; 100 ps
tcoupl = V-rescale
ref_t = 300
tau_t = 0.1
pcoupl = Berendsen
ref_p = 1.0
tau_p = 2.0
compressibility = 4.5e-5
constraints = h-bonds
```

**Production MD (md.mdp)**:
```
integrator = md
dt = 0.002
nsteps = 5000000  ; 10 ns
tcoupl = V-rescale
ref_t = 300
tau_t = 0.1
pcoupl = Parrinello-Rahman
ref_p = 1.0
tau_p = 2.0
compressibility = 4.5e-5
nstxout-compressed = 5000
nstenergy = 500
constraints = h-bonds
coulombtype = PME
rcoulomb = 1.0
rvdw = 1.0
pbc = xyz
```

### Submit to Bohrium

```
Bohrium(action="submit",
  input_dir="<dir_with_gro_top_mdp>",
  image="registry.dp.tech/dptech/gromacs:2022.2",
  cmd="gmx grompp -f em.mdp -c solvated.gro -p topol.top -o em.tpr && gmx mdrun -v -deffnm em && gmx grompp -f nvt.mdp -c em.gro -r em.gro -p topol.top -o nvt.tpr && gmx mdrun -v -deffnm nvt && gmx grompp -f npt.mdp -c nvt.gro -r nvt.gro -p topol.top -o npt.tpr && gmx mdrun -v -deffnm npt && gmx grompp -f md.mdp -c npt.gro -r npt.gro -p topol.top -o md.tpr && gmx mdrun -v -deffnm md > log 2>&1",
  machine="c32_m128_cpu")
```

Or chain steps in a `run.sh` shell script and submit with `cmd="bash run.sh > log 2>&1"`.

## Step 5: Analysis (md-analysis skill)

After job completion, download results and analyze:

```bash
uv run python matmaster/skills/playground-skills/md-analysis/scripts/analyze_gmx.py \
  rmsd --structure md.tpr --trajectory md.xtc --output rmsd.xvg

uv run python matmaster/skills/playground-skills/md-analysis/scripts/analyze_gmx.py \
  gyrate --structure md.tpr --trajectory md.xtc --output gyrate.xvg

uv run python matmaster/skills/playground-skills/md-analysis/scripts/analyze_gmx.py \
  energy --energy md.edr --output energy.xvg --terms "Potential Temperature Pressure"
```

## Common Issues & Fixes

| Issue | Cause | Fix |
|-------|-------|-----|
| Topology mismatch | Atom counts differ between .gro and .top | Regenerate topology or check solvation step |
| Box too small | Polymer extends beyond PBC minimum image | Use editconf with larger box (> 2×rcoulomb padding) |
| Force field missing | `.top` references unavailable FF | Include FF `.itp` files in input directory |
| Coordinate explosion | Bad initial geometry | Run longer EM with `emtol=100`; check for overlaps |
| genion fails | Missing .tpr for ion insertion | Run grompp first to generate .tpr |

## Deliverables Checklist

For a complete polymer simulation task, deliver:
- [ ] Polymer structure file (.pdb/.gro)
- [ ] Topology file (.top) with correct force field
- [ ] MDP files (em, nvt, npt, md)
- [ ] Submission command or run.sh
- [ ] Analysis results (RMSD, Rg, energy plots) if simulation completed
