# Polymer MD Simulation — End-to-End Workflow Reference

This reference covers the complete pipeline from polymer definition to GROMACS MD simulation on Bohrium.

## Overview

Polymer simulation requires more steps than small-molecule MD. The typical workflow:

```
Monomer SMILES → Polymer chain (poly-generator)
  → 3D structure (.pdb/.mol2) (generate_3d.py)
  → GROMACS topology (.top) (poly-forcefield OR acpype/manual)
  → Simulation box (.gro) (gromacs-system-prep: editconf)
  → Solvation (if needed) (gromacs-system-prep: solvate + genion)
  → Energy minimization (Bohrium)
  → NVT equilibration (Bohrium)
  → NPT equilibration (Bohrium)
  → Production MD (Bohrium)
  → Analysis (md-analysis)
```

**Save-early rule**: Write each intermediate file to workspace immediately after generation. Partial credit is awarded for delivered structure files, topology, and MDP even if MD does not complete.

## Step 1: Build Polymer Structure

Use **poly-generator** skill:

```bash
# Build polymer from monomer SMILES
uv run python matmaster/skills/playground-skills/poly-generator/scripts/build_polymer.py \
  --monomers '{"A": "[*]CC(=O)O[*]"}' --sequence A10 --output-json polymer.json

# Generate 3D structure from the SMILES
uv run python matmaster/skills/playground-skills/poly-generator/scripts/generate_3d.py \
  --smiles "<polymer_smiles_from_step_above>" --output polymer.pdb --format pdb
```

**Key rules**:
- Use `[*]` markers in monomer SMILES for connecting points
- For homopolymer with N repeats: `--sequence AN` (e.g., `A10`)
- For copolymers: `--sequence A5B5` (block) or `--mode random --degree 10` (random)
- Always generate 3D structure as `.pdb` format for GROMACS compatibility

## Step 2: Generate Topology

### Option A: poly-forcefield (for supported chemistries)

```bash
uv run python matmaster/skills/playground-skills/poly-forcefield/scripts/generate_gmx_top.py \
  --smiles "<polymer_smiles>" --output polymer.top --molecule-name POLY
```

If the topology generation fails (unsupported chemistry), report the limitation and try Option B.

### Option B: Manual GAFF-based topology

For polymers outside poly-forcefield scope, write a GROMACS-compatible topology manually:
1. Use knowledge of GAFF/OPLS-AA atom types for the polymer functional groups
2. Write a minimal `.top` file with `[moleculetype]`, `[atoms]`, `[bonds]`, `[pairs]`, `[angles]`, `[dihedrals]`, and `[system]` sections
3. Include appropriate force field parameters in the `[defaults]` section

### Topology validation
- Check that atom count in `.top` matches `.gro`/`.pdb`
- Verify all bond types are assigned
- Check total charge is as expected (typically 0 for neutral polymers)

## Step 3: Set Up Simulation Box

Use **gromacs-system-prep** skill:

```bash
# Convert PDB to GRO and set box
uv run python matmaster/skills/playground-skills/gromacs-system-prep/scripts/prepare_gmx.py \
  editconf --input polymer.pdb --output polymer.gro --box-type cubic --distance 1.2

# Solvate (for solution-phase simulations)
uv run python matmaster/skills/playground-skills/gromacs-system-prep/scripts/prepare_gmx.py \
  solvate --input polymer.gro --topology polymer.top --output solvated.gro

# Add ions if needed (requires a .tpr first)
uv run python matmaster/skills/playground-skills/gromacs-system-prep/scripts/prepare_gmx.py \
  genion --input ions.tpr --topology polymer.top --output ionized.gro --neutral
```

### Box setup by simulation type

| Scenario | Box type | Padding (nm) | Solvation | Notes |
|----------|----------|-------------|-----------|-------|
| Bulk polymer (melt) | cubic | 0.5–1.0 | No | Multiple chains, high density |
| Polymer in solvent | cubic/dodecahedron | 1.0–1.5 | Yes (water/TIP3P) | Add ions for charge neutrality |
| Polymer membrane | rectangular | custom | One side | Extend z for vacuum/solvent |
| Polymer adhesion/interface | rectangular | custom | Optional | Two layers with defined spacing |
| Single chain properties | cubic | 1.5–2.0 | Yes | Dilute solution |

## Step 4: Generate MDP Files

Use **gromacs-system-prep** MDP generation:

```bash
# Energy minimization
uv run python matmaster/skills/playground-skills/gromacs-system-prep/scripts/prepare_gmx.py \
  write-mdp --task em --output em.mdp

# NVT equilibration (with position restraints if needed)
uv run python matmaster/skills/playground-skills/gromacs-system-prep/scripts/prepare_gmx.py \
  write-mdp --task nvt --output nvt.mdp

# NPT equilibration
uv run python matmaster/skills/playground-skills/gromacs-system-prep/scripts/prepare_gmx.py \
  write-mdp --task npt --output npt.mdp

# Production MD
uv run python matmaster/skills/playground-skills/gromacs-system-prep/scripts/prepare_gmx.py \
  write-mdp --task md --output md.mdp
```

### Polymer-specific MDP adjustments

Polymer systems often need longer equilibration and specific settings:

**Energy Minimization** (`em.mdp`):
```
integrator  = steep
emtol       = 1000.0    ; kJ/mol/nm — may need higher tolerance for polymers
emstep      = 0.01
nsteps      = 50000     ; More steps for large polymer systems
```

**NVT Equilibration** (`nvt.mdp`):
```
integrator  = md
dt          = 0.001     ; 1 fs — use smaller timestep initially for polymers
nsteps      = 100000    ; 100 ps NVT
tcoupl      = V-rescale
tc-grps     = System
tau_t       = 0.1
ref_t       = 300
gen_vel     = yes
gen_temp    = 300
constraints = h-bonds
constraint_algorithm = lincs
```

**NPT Equilibration** (`npt.mdp`):
```
integrator  = md
dt          = 0.002     ; 2 fs with constraints
nsteps      = 500000    ; 1 ns NPT — polymers need longer equilibration
tcoupl      = V-rescale
tc-grps     = System
tau_t       = 0.1
ref_t       = 300
pcoupl      = Berendsen     ; Berendsen for equilibration
pcoupltype  = isotropic
tau_p       = 2.0
ref_p       = 1.0
compressibility = 4.5e-5
constraints = h-bonds
constraint_algorithm = lincs
```

**Production MD** (`md.mdp`):
```
integrator  = md
dt          = 0.002
nsteps      = 5000000   ; 10 ns production (adjust per task)
tcoupl      = V-rescale
tc-grps     = System
tau_t       = 0.1
ref_t       = 300
pcoupl      = Parrinello-Rahman   ; PR for production
pcoupltype  = isotropic
tau_p       = 2.0
ref_p       = 1.0
compressibility = 4.5e-5
coulombtype = PME
rcoulomb    = 1.0
rvdw        = 1.0
pbc         = xyz
nstxout-compressed = 5000
nstenergy   = 500
nstlog      = 1000
constraints = h-bonds
constraint_algorithm = lincs
continuation = yes
```

## Step 5: Submit to Bohrium

Chain EM → NVT → NPT → MD in a single submission script:

```bash
#!/bin/bash
# run.sh — chain all four stages

# Energy minimization
gmx grompp -f em.mdp -c system.gro -p topol.top -o em.tpr -maxwarn 3
gmx mdrun -v -deffnm em

# NVT equilibration
gmx grompp -f nvt.mdp -c em.gro -p topol.top -o nvt.tpr -maxwarn 3
gmx mdrun -v -deffnm nvt

# NPT equilibration
gmx grompp -f npt.mdp -c nvt.gro -p topol.top -o npt.tpr -maxwarn 3
gmx mdrun -v -deffnm npt

# Production MD
gmx grompp -f md.mdp -c npt.gro -p topol.top -o md.tpr -maxwarn 3
gmx mdrun -v -deffnm md
```

Submit:
```
Bohrium(action="submit",
        input_dir="<workspace_dir>",
        image="registry.dp.tech/dptech/gromacs:2022.2",
        cmd="bash run.sh > log 2>&1",
        machine="c32_m128_cpu")
```

**Tip**: If time budget is limited, submit at least EM + NVT as a shorter run. Partial simulation results (equilibrated system) still have value.

## Step 6: Analysis

After download, use **md-analysis** skill:

```bash
uv run python matmaster/skills/playground-skills/md-analysis/scripts/analyze_gmx.py \
  energy --input md.edr --terms "Potential Temperature Pressure Density"

uv run python matmaster/skills/playground-skills/md-analysis/scripts/analyze_gmx.py \
  rdf --trajectory md.xtc --structure md.tpr --output rdf.xvg

uv run python matmaster/skills/playground-skills/md-analysis/scripts/analyze_gmx.py \
  gyrate --trajectory md.xtc --structure md.tpr --output gyrate.xvg
```

### Common polymer analysis properties

| Property | GROMACS tool | Notes |
|----------|-------------|-------|
| Radius of gyration (Rg) | `gmx gyrate` | Chain compactness |
| End-to-end distance | `gmx distance` | Requires index groups |
| Mean square displacement (MSD) | `gmx msd` | Diffusion coefficient |
| Radial distribution function | `gmx rdf` | Inter/intra-molecular structure |
| Density | `gmx energy` | Equilibrium bulk density |
| Glass transition (Tg) | density vs T | Multiple NPT at different T |
| Hydrogen bonds | `gmx hbond` | For polar polymers |

## Common Polymer Task Types

### Donor/Acceptor materials
- Build conjugated polymer with proper π-system
- May need GAFF or custom FF for conjugated backbone
- Key property: Rg, persistence length, morphology

### Membrane simulation
- Build polymer membrane structure
- Set up with explicit solvent on one or both sides
- Key: permeability, water transport, mechanical properties

### Polymer rheology
- Bulk polymer melt simulation
- Multiple chains (5–20) in periodic box
- Key: viscosity from Green-Kubo, MSD, chain dynamics

### Ion hopping/transport
- Polymer electrolyte (e.g. PEO + Li salt)
- Key: ion MSD, coordination number, hopping mechanism

### Adhesion/interface
- Two-layer system with defined interface
- Use `gmx insert-molecules` or manual coordinate stacking
- Key: adhesion energy, interfacial width, density profiles

## Troubleshooting

| Problem | Likely Cause | Fix |
|---------|-------------|-----|
| grompp "atom type not found" | Missing force field parameters | Check `.top` includes all needed atom types |
| Simulation blows up (LINCS warning) | Bad initial structure | Run longer EM; use smaller dt initially |
| Density too low after NPT | Insufficient equilibration | Run longer NPT (≥ 2 ns for polymers) |
| poly-forcefield fails | Chemistry outside current scope | Use manual topology or GAFF-based approach |
| Box too small warning | Polymer extends beyond half-box | Increase box size; check minimum image convention |
