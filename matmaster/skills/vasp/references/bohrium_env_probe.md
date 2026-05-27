# Bohrium Environment Probe

Before submitting a real VASP job to an unfamiliar image, run a lightweight
diagnostic to avoid trial-and-error SIGSEGV debugging.

## Probe Script

Create a directory with a single file `probe.sh`:

```bash
#!/bin/bash
echo "=== VASP binary ==="
which vasp_std vasp_gam vasp_ncl 2>/dev/null || find /opt -name "vasp_std" 2>/dev/null
echo "=== Intel env ==="
ls /opt/intel/oneapi/setvars.sh 2>/dev/null && echo "setvars.sh found"
cat ~/.bashrc 2>/dev/null | grep -i "source.*intel\|setvars\|compilervars"
echo "=== Stack limit ==="
ulimit -s
echo "=== MPI ==="
which mpirun && mpirun --version 2>&1 | head -3
```

## Submit

```
Bohrium(action="submit", image="<vasp_image>", machine="c64_m256_cpu",
        input_dir="<probe_dir>", cmd="bash probe.sh > log 2>&1")
```

Poll immediately — no sleep needed, finishes in seconds.

## Interpret Results

| Observation | Action |
|-------------|--------|
| `setvars.sh found` | Add `source /opt/intel/oneapi/setvars.sh > /dev/null 2>&1` to cmd |
| Stack limit < 65536 (e.g., 8192) | Add `ulimit -s unlimited` |
| vasp_std not in PATH | Add `export PATH=/opt/vasp.../bin:$PATH` |
| Intel env already in `.bashrc` | Still source explicitly — Bohrium runs cmd non-interactively |

## Standard cmd Template (after probe)

```bash
source /opt/intel/oneapi/setvars.sh > /dev/null 2>&1 && ulimit -s unlimited && OMP_NUM_THREADS=1 mpirun -np 16 vasp_std > log 2>&1
```

Adjust binary (`vasp_gam`/`vasp_ncl`) and `-np` based on task and probe results.
