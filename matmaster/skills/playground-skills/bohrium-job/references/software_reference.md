# Bohrium Software Reference

> These are the most commonly used defaults. When you need a different version or software not listed here, run `list_images.py --keyword <software>` to find the exact image address.

### CP2K

| Item | Value |
|------|-------|
| Image | `registry.dp.tech/dptech/cp2k:2024.1` |
| Machine | `c32_m128_cpu` (32 cores, 128 GB RAM) |
| Command (32-core) | `OMP_NUM_THREADS=1 mpirun -np 32 cp2k.popt -i input.inp > log 2>&1` |

### Quantum ESPRESSO (pw.x)

| Item | Value |
|------|-------|
| Image | `registry.dp.tech/dptech/quantum-espresso:7.1` |
| Machine | `c32_m128_cpu` |
| Command | `OMP_NUM_THREADS=1 mpirun -np 32 pw.x -i pw.in > log 2>&1` |

### ABINIT

| Item | Value |
|------|-------|
| Image | `registry.dp.tech/dptech/dp/native/prod-19853/abinit:v9.10.3_pp` |
| Machine | `c32_m128_cpu` |
| Command | `OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1 OMPI_MCA_rmaps_base_oversubscribe=1 OMP_NUM_THREADS=1 mpirun -np 32 abinit run.abi > log 2>&1` |

> The container runs as root; `OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1` are required.

### LAMMPS

| Item | Value |
|------|-------|
| Image | `registry.dp.tech/dptech/lammps-agent:03810da8` |
| Machine | `c16_m64_1 * NVIDIA 4090` (GPU node) |
| Command | `lmp -in lammps.in > log 2>&1` |

### ORCA

| Item | Value |
|------|-------|
| Image | `registry.dp.tech/dptech/dp/native/prod-19853/orca:v6.1.1` |
| Machine | `c32_m128_cpu` |
| Command | `OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1 OMPI_MCA_rmaps_base_oversubscribe=1 /opt/orca611_avx2/orca input.inp > log 2>&1` |

> ORCA is run via absolute path (not via mpirun, not in PATH).

### GROMACS

| Item | Value |
|------|-------|
| Image | `registry.dp.tech/dptech/gromacs:2022.2` |
| Machine | `c32_m128_cpu` |
| Command | `gmx grompp -f md.mdp -c conf.gro -p topol.top -o run.tpr && gmx mdrun -v -deffnm run > log 2>&1` |

> For GPU: `--machine "c6_m60_1 * NVIDIA 4090"` and add `-gpu_id 0` to `mdrun`.

### PySCF

| Item | Value |
|------|-------|
| Image | `registry.dp.tech/dptech/dp/native/prod-19853/pyscf-geometric:dev-260305` |
| Machine | `c32_m128_cpu` |
| Command | `python run_pyscf.py > log 2>&1` |

### ABACUS

| Item | Value |
|------|-------|
| Image | `registry.dp.tech/dptech/abacus:LTSv3.10.1` |
| Machine | `c32_m128_cpu` |
| Command | `OMP_NUM_THREADS=1 mpirun -np 16 abacus > log 2>&1` |

> `-np` = **half the CPU core count** (32 → 16).
> Pseudopotentials: download from AIS Square ABACUS-APNS-PPORBs-v1.

### PyATB

| Item | Value |
|------|-------|
| Image | *(query with `list_images.py --keyword pyatb`)* |
| Machine | `c32_m128_cpu` |
| Command | `python run_pyatb.py > log 2>&1` |
