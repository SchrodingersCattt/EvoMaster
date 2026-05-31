---
name: gromacs
description: "Use to RUN GROMACS molecular dynamics (NVT/NPT, energy minimization, FEP, enhanced sampling, solvation, ion addition, etc.) - biomolecular / soft-matter focus. DO NOT use for other engines (ABACUS / LAMMPS / GPUMD / VASP / CP2K) or for non-MD tasks."
skill_type: operator
---

# GROMACS Skill

## Bohrium Config

| Item | CPU (default) | GPU |
|------|--------------|-----|
| image | `registry.dp.tech/dptech/dp/native/hub/mrdic2/a1:1.0.1-1779698340` | same |
| machine | `c64_m256_cpu` | `c8_m32_1 * NVIDIA 4090` |
| cmd | `bash run.sh > log 2>&1` | same |

Image has **`gmx` only** (GROMACS 2024.2, thread-MPI + CUDA). No `gmx_mpi`. Do NOT use `mpirun`.

## Key Rules

1. **run.sh preamble** — every script starts with:
   ```bash
   #!/bin/bash
   set -e
   export PATH="/usr/local/gmx-2024.2/bin:$PATH"
   ```
2. **Chain all steps in one script** — EM → NVT → NPT in a single `run.sh`. Each Bohrium submission has ~1 min scheduling overhead.
3. **DO NOT run `gmx` locally** — all GROMACS commands go in the submitted `run.sh`.
4. **`grompp -maxwarn 3`** — always pass to avoid abort on non-fatal notes.
5. **`genion` requires a `.tpr`** — run `grompp` first, then `echo "SOL" | gmx genion -s ions.tpr ...`.
6. **`gmx solvate -p` requires topology to exist** — create topology (via `pdb2gmx`) before calling solvate with `-p`.
7. **Use provided files directly** — if user gives `.gro` + `.top` + `.mdp`, reference as-is in `run.sh`.
8. **Interactive commands via pipe** — `echo "GROUP" | gmx genion ...`, `echo "0" | gmx make_ndx ...`.

## Parallel Execution

```bash
# CPU (32 physical cores): auto-detect is fine for short jobs
gmx mdrun -deffnm em -v

# CPU (explicit): maximize throughput for long MD
gmx mdrun -deffnm md -ntmpi 1 -ntomp 32

# GPU:
gmx mdrun -deffnm md -ntmpi 1 -ntomp 8 -gpu_id 0 -nb gpu -pme gpu
```

## System Building Commands (inside run.sh)

```bash
GMXTOP=$(find /usr/local -type d -name "top" -path "*/gromacs/*" 2>/dev/null | head -1)
```

| Step | Command |
|------|---------|
| Topology from PDB | `gmx pdb2gmx -f input.pdb -o processed.gro -water spce` |
| Edit box | `gmx editconf -f input.gro -o box.gro -d 1.0 -bt cubic` |
| Solvate | `gmx solvate -cp box.gro -cs $GMXTOP/spc216.gro -o solvated.gro -p topol.top` |
| Ion prep | `gmx grompp -f ions.mdp -c solvated.gro -p topol.top -o ions.tpr -maxwarn 3` |
| Add ions | `echo "SOL" \| gmx genion -s ions.tpr -o ionized.gro -p topol.top -pname NA -nname CL -neutral` |

## Execution Workflow

1. Prepare input files locally (MDP, structure, topology — or use provided files)
2. Write `run.sh` (preamble + system building if needed + `grompp` + `mdrun`)
3. Place all files in one directory
4. `Bohrium(action="submit", input_dir="<dir>", image="<image from Config>", cmd="bash run.sh > log 2>&1")`
5. `Bohrium(action="poll", job_id=<id>)` — repeat until Finished/Failed
6. `Bohrium(action="download", job_id=<id>, result_dir="<output_dir>")`

## Ligand Parameterization

For small-molecule force field parameterization (GAFF/GAFF2/OPLS-AA) → `references/ligand_parameterization.md`

## Physical Checks & MDP Defaults

For timestep, thermostat, barostat, cutoff, box size rules → `references/physical_checks.md`

## Post-Processing

After job completion, use the **md-analysis** skill for trajectory analysis (RMSD, RMSF, RDF, MSD, H-bonds, energy).
