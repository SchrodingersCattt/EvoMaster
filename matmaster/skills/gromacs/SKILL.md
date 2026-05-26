---
name: gromacs
description: "Use to RUN GROMACS molecular dynamics (NVT/NPT, energy minimization, FEP, enhanced sampling, solvation, ion addition, etc.) - biomolecular / soft-matter focus. DO NOT use for other engines (ABACUS / LAMMPS / GPUMD / VASP / CP2K) or for non-MD tasks."
skill_type: operator
---

# GROMACS Skill

GROMACS is a high-performance molecular dynamics package primarily designed for simulations of proteins, lipids, and nucleic acids, but widely used for any system with classical force fields.

## Bohrium Submission Config

| Item | Default Value (CPU) |
|------|---------------------|
| image | `registry.dp.tech/dptech/dp/native/hub/mrdic2/a1:1.0.1-1779698340` |
| machine | `c32_m128_cpu` (32 cores, 128 GB RAM) |
| cmd | `bash run.sh > log 2>&1` |

| Item | GPU Alternative |
|------|-----------------|
| machine | `c6_m60_1 * NVIDIA 4090` |
| cmd | `bash run.sh > log 2>&1` (add `-gpu_id 0` to mdrun in script) |

> **The Bohrium GROMACS image has `gmx_mpi` only (not `gmx`).** Do NOT use `-ntmpi` (thread-MPI is not compiled in).
> Adjust `grompp` arguments to match actual filenames.
> For GPU options: `Bohrium(action="list_machines", machine_type="gpu", keyword="4090")`.
> For different GROMACS versions: `Bohrium(action="list_images", keyword="gromacs")`.
> When submitting multiple systems in parallel, use **distinct file names** (e.g. `sysA_init.gro`, `sysB_init.gro`) to avoid Bohrium upload cache collisions.

## Input Preparation

GROMACS uses three core files: **topology** (`.top`), **coordinates** (`.gro`), and **simulation parameters** (`.mdp`).

### Using render_input.py (for .mdp generation)

```bash
# Generate mdp file
uv run python scripts/render_input.py --software gromacs --task md --output md.mdp
```

### System Building (inside `run.sh`, executed on Bohrium)

These commands run inside the submitted `run.sh` — `gmx_mpi` is only available in the Bohrium image, NOT locally.

The image's shared data lives at `$GMXLIB` (typically `/usr/local/gromacs/share/gromacs/top/`). Topology `#include` directives (e.g. `#include "oplsaa.ff/forcefield.itp"`) resolve against this path automatically. When referencing data files explicitly (e.g. `spc216.gro`), use the full path:

```bash
GMXTOP=$(find /usr/local -type d -name "top" -path "*/gromacs/*" | head -1)
```

| Step | Command | Purpose |
|------|---------|---------|
| Topology from PDB | `gmx_mpi pdb2gmx -f input.pdb -o processed.gro -water spce` | Generate `.top` + `.gro` from PDB |
| Edit box | `gmx_mpi editconf -f input.gro -o box.gro -d 1.0 -bt cubic` | Set box with padding |
| Solvate | `gmx_mpi solvate -cp box.gro -cs $GMXTOP/spc216.gro -o solvated.gro -p topol.top` | Add solvent (`-p` updates existing `.top`) |
| Ion prep | `gmx_mpi grompp -f ions.mdp -c solvated.gro -p topol.top -o ions.tpr -maxwarn 3` | Prepare `.tpr` for genion |
| Add ions | `echo "SOL" \| gmx_mpi genion -s ions.tpr -o ionized.gro -p topol.top -pname NA -nname CL -neutral` | Neutralize system |
| Index groups | `gmx_mpi make_ndx -f conf.gro -o index.ndx` | Create custom groups |

### Ready-to-run files

If the user provides `.gro` + `.top` + `.mdp` (or a pre-built `.tpr`), skip preparation.

## Task Types

| Task | Description | Key MDP Settings |
|------|-------------|------------------|
| em | Energy minimization | `integrator = steep`, `emtol`, `nsteps` |
| nvt | NVT equilibration | `integrator = md`, `tcoupl = V-rescale`, `ref_t` |
| npt | NPT equilibration | `tcoupl = V-rescale`, `pcoupl = Parrinello-Rahman`, `ref_p` |
| md | Production MD | `integrator = md`, `nsteps`, `dt`, output frequencies |
| fep | Free energy perturbation | `free_energy = yes`, `init_lambda_state`, `fep_lambdas` |

## Required Files

- **Topology** (`.top`): force field parameters, molecule definitions
- **Coordinates** (`.gro` or `.pdb`): system coordinates, box dimensions
- **MDP file** (`.mdp`): simulation parameters
- **Force field**: referenced in `.top`; typically AMBER, CHARMM, OPLS-AA, GROMOS
- **Index file** (`.ndx`): optional, for custom groups
- **Restraint files** (`.itp`): optional position restraints

## Physical Checks

- **timestep**: 2 fs with LINCS constraints on H-bonds (`constraint_algorithm = lincs`); 1 fs without constraints
- **Thermostat**: V-rescale (`tcoupl = V-rescale`, `tau_t = 0.1`) for equilibration and production
- **Barostat**: Berendsen for equilibration, Parrinello-Rahman for production NPT (`tau_p = 2.0`)
- **Cutoffs**: typically `rcoulomb = 1.0`, `rvdw = 1.0` nm; PME for long-range electrostatics (`coulombtype = PME`)
- **Neighbor list**: `nstlist = 10`, `ns_type = grid`, `verlet-buffer-tolerance` for Verlet scheme
- **Output frequency**: `nstxout-compressed = 5000` (every 10 ps at dt=2fs) for trajectory; `nstenergy = 500` for energy
- **Box size**: minimum image convention requires box dimension > 2 * rcoulomb; check `gmx editconf -d 1.0` padding
- **Periodic boundary conditions**: `pbc = xyz` for standard 3D periodic

## Key Rules

1. **Chain all steps in one script** — When a workflow has multiple sequential steps (EM → NVT → NPT), write a single `run.sh` that executes them all. Always start with `set -e` so the script aborts on first failure. Each Bohrium submission has ~1 min overhead for scheduling; avoid submitting steps separately.
2. **`gmx_mpi solvate -p` requires the topology to exist** — The `-p` flag *updates* an existing `.top` with solvent molecule counts. If the topology doesn't exist yet, omit `-p` and manually add `[ molecules ]` entries, or create the topology first.
3. **Use provided input files directly** — If the user provides `.gro` + `.top` + `.mdp`, do NOT recreate them. Write a `run.sh` that references them as-is for `grompp`.
4. **`grompp -maxwarn 3`** — Always pass `-maxwarn 3` (at minimum) to avoid grompp aborting on non-fatal notes (e.g. overridden mdp parameters).
5. **`genion` requires a `.tpr`** — Run `grompp` first to produce the `.tpr`, then feed it to `genion`.
6. **DO NOT run `gmx_mpi` locally** — `gmx_mpi` is only available in the Bohrium image. All GROMACS commands (including system building) must be in the submitted `run.sh`.
7. **Interactive selections via pipe** — Commands that need interactive group input (e.g. `genion`, `make_ndx`) must use `echo "GROUP" | gmx_mpi ...` in the script.
8. **Use the skill default image** — Submit with `registry.dp.tech/dptech/dp/native/hub/mrdic2/a1:1.0.1-1779698340` unless the user explicitly requests another image.

## Submission Workflow

1. Prepare input files locally (MDP, structure, topology — or use provided files)
2. Write a `run.sh` that chains all steps (system building if needed + grompp + mdrun)
3. Ensure all files (`.gro`, `.top`, `.mdp`, `run.sh`) are in one directory
4. Submit:
   `Bohrium(action="submit", input_dir="<dir>", image="registry.dp.tech/dptech/dp/native/hub/mrdic2/a1:1.0.1-1779698340", cmd="bash run.sh > log 2>&1")`
5. Poll: `Bohrium(action="poll", job_id=<id>)`
6. Download: `Bohrium(action="download", job_id=<id>, result_dir="<output_dir>")`

## Post-Processing

After job completion, use the **md-analysis** skill for trajectory analysis (RMSD, RMSF, gyration radius, MSD, RDF, H-bonds, energy).

## Reference

Official documentation: `site:manual.gromacs.org`
