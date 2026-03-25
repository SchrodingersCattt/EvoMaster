---
name: bohrium-job
description: "Submission engine for Bohrium HPC. Receives a prepared input directory (output_dir from input-manual-helper, or a directory containing user-provided ready-to-run input files) and supports split submission and monitoring via submit_job.py and poll_job.py. Covers CP2K, QE, ABINIT, LAMMPS, ORCA, GROMACS and any other software available on Bohrium."
skill_type: operator
---

# Bohrium Job Skill

Submit an input directory to Bohrium HPC. There are two entry paths:
- **Normal path**: call `input-manual-helper` first to generate the input files, then pass the resulting `output_dir` here.
- **User-provided file path**: if the user has already supplied a complete, ready-to-run input file, **skip `input-manual-helper`** and pass the directory containing that file directly as `--input-dir`.

## Workflow

1. **Confirm input directory** — Either get the `output_dir` returned by `input-manual-helper`, or identify the directory that already contains the user-provided input file. List its contents to confirm the main input filename needed for `--cmd`.
2. **Look up image / machine / command** — Check the Software Reference table below. If the software **is not listed**, or you need a different version/machine, you **MUST** run `list_images.py` and/or `list_machines.py` to query the Bohrium platform — **never** conclude that a software or machine is unavailable without running these scripts first.
3. **Submit only** (returns `job_id`):
   ```
   use_skill bohrium-job run_script submit_job.py \
     --input-dir <output_dir from input-manual-helper> \
     --image <image> \
     --cmd "<command>" \
     [--machine <machine_type>] \
     [--job-name <name>] \
     [--software <software>]
   ```
   The script: packages and uploads input dir → three-step job create/upload/add.
   stdout JSON: `{"success": true, "job_id": ..., "bohr_job_id": ..., "status": "Submitted"}`.

  **Log filename convention (MUST):** the run command in `--cmd` must redirect stdout/stderr to a file named exactly `log` (for example: `> log 2>&1`). Do not rename it to custom names like `caffeine.out`.

4. **Monitor + download** (second step) — MUST use `poll_job.py` script, **NOT** the built-in `monitor_job` tool:
   ```json
   {
     "action": "run_script",
     "skill_name": "bohrium-job",
     "script_name": "poll_job.py",
    "script_args": "--job-id <job_id> [--max-polls 2880] [--poll-interval 30] [--result-dir results/run_xxx]",
    "script_timeout": 86400
  }
  ```
  > **IMPORTANT**: `script_timeout` is a **`use_skill` tool parameter** (not part of `script_args`). Always set it to `max_polls × poll_interval` (default 2880 × 30 = **86400 s**). Without it, the session default timeout (60 s) will kill the script before the first poll completes.
  >
  > ⚠️ **Do NOT reduce `--max-polls` below 2880** unless the user explicitly requests a shorter timeout. HPC jobs can take many hours; underestimating will cause poll timeout before the job finishes. When in doubt, keep the default (`--max-polls 2880`, `script_timeout 86400`).

   The script: polls `/openapi/v1/job/{id}` until terminal status; downloads `out.zip` via `resultUrl` and extracts (for both `Finished` and `Failed`); reads `log_tail` from the local `log` file in the extracted directory.
   stdout JSON (success): `{"success": true, "job_id": ..., "status": "Finished", "result_dir": "...", "files": [...], "log_tail": "..."}`.
   stdout JSON (failed): `{"success": false, "job_id": ..., "status": "Failed", "result_dir": "...", "files": [...], "log_tail": "<error from log file>", "error": "..."}`.

> **CRITICAL**: Do NOT call the built-in tool `monitor_job` for this workflow. Always use `use_skill bohrium-job run_script poll_job.py`. The built-in `monitor_job` tool is a different system and will not produce the expected output format.

## Dynamic Discovery: Images & Machines

**These scripts query the live Bohrium platform.** You MUST use them whenever the Software Reference table does not cover the requested software, version, or machine type. Do NOT assume a software/image/machine is unavailable — always query first.

### list_images.py — Query available Docker images

```
use_skill bohrium-job run_script list_images.py \
  [--keyword <name>] \
  [--max-results 20]
```

- `--keyword`: Filter by image name (case-insensitive), e.g. `--keyword gromacs`
- `--max-results`: Limit returned entries (default: 20)

Output JSON: `{"success": true, "total_found": N, "returned": M, "images": [{"id": ..., "name": "...", "versions": [{"url": "registry.dp.tech/...", "version": "...", "resourceType": "CPU GPU", ...}]}]}`

Use the `url` value from the versions list as the `--image` argument to `submit_job.py`.

### list_machines.py — Query available machine types

```
use_skill bohrium-job run_script list_machines.py \
  [--type cpu|gpu] \
  [--keyword <name>] \
  [--max-results 50]
```

- `--type`: `cpu` or `gpu` (default: `cpu`)
- `--keyword`: Filter by machine name, e.g. `--keyword c32`
- `--max-results`: Limit returned entries (default: 50)

Output JSON: `{"success": true, "type": "cpu", "machines": [{"skuEnName": "c32_m64_cpu", "cpuCoreNum": 32, "memory": 64, "price": 2.56, "hasStock": true, ...}]}`

Use the `skuEnName` value as the `--machine` argument to `submit_job.py`.

## Software Reference

> **Tip**: These are the most commonly used defaults. When you need a different version or software not listed here, run `list_images.py --keyword <software>` to find the exact image address.

### CP2K

| Item | Value |
|------|-------|
| Image | `registry.dp.tech/dptech/cp2k:2024.1` |
| Machine | `c32_m128_cpu` (32 cores, 128 GB RAM) |
| Command (32-core) | `OMP_NUM_THREADS=1 mpirun -np 32 cp2k.popt -i input.inp > log 2>&1` |

> Check actual input filename in `output_dir`; replace `input.inp` if named differently.

### Quantum ESPRESSO (pw.x)

| Item | Value |
|------|-------|
| Image | `registry.dp.tech/dptech/quantum-espresso:7.1` |
| Machine | `c32_m128_cpu` (32 cores, 128 GB RAM) |
| Command (32-core) | `OMP_NUM_THREADS=1 mpirun -np 32 pw.x -i pw.in > log 2>&1` |

> Check actual input filename in `output_dir`; replace `pw.in` if named differently.

### ABINIT

| Item | Value |
|------|-------|
| Image | `registry.dp.tech/dptech/dp/native/prod-19853/abinit:v9.10.3_pp` |
| Machine | `c32_m128_cpu` (32 cores, 128 GB RAM) |
| Command (32-core) | `OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1 OMPI_MCA_rmaps_base_oversubscribe=1 OMP_NUM_THREADS=1 mpirun -np 32 abinit run.abi > log 2>&1` |

> The container runs as root; `OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1` are required or mpirun will refuse to start.
> `OMPI_MCA_rmaps_base_oversubscribe=1` prevents "not enough slots" errors when the container reports fewer slots than `-np`.
> Check actual `.abi` filename in `output_dir`; replace `run.abi` with the actual name.

### LAMMPS

| Item | Value |
|------|-------|
| Image | `registry.dp.tech/dptech/lammps-agent:03810da8` |
| Machine | `c16_m64_1 * NVIDIA 4090` (GPU node) |
| Command | `lmp -in lammps.in > log 2>&1` |

> Check actual `.in` / `.lammps` filename in `output_dir`; replace `lammps.in` if named differently.

### ORCA

| Item | Value |
|------|-------|
| Image | `registry.dp.tech/dptech/dp/native/prod-19853/orca:v6.1.1` |
| Machine | `c32_m128_cpu` (32 cores, 128 GB RAM) |
| Command | `OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1 OMPI_MCA_rmaps_base_oversubscribe=1 /opt/orca611_avx2/orca input.inp > log 2>&1` |

> ORCA is run via absolute path (not via mpirun, not in PATH). Check actual `.inp` filename in `oss_downloaded_files/`; replace `input.inp` with the actual filename (e.g. `1773128180_caffeine.inp`).

### GROMACS

| Item | Value |
|------|-------|
| Image | `registry.dp.tech/dptech/gromacs:2022.2` |
| Machine | `c32_m128_cpu` (32 cores, 128 GB RAM) |
| Command | `gmx grompp -f md.mdp -c conf.gro -p topol.top -o run.tpr && gmx mdrun -v -deffnm run > log 2>&1` |

> Adjust the `gmx grompp` and `gmx mdrun` arguments to match actual input filenames in the directory.
> For GPU-accelerated GROMACS runs, use `--machine "c6_m60_1 * NVIDIA 4090"` and add `-gpu_id 0` to `mdrun`. Run `list_machines.py --type gpu --keyword 4090` to see all GPU options.
> If you need a different GROMACS version, run `list_images.py --keyword gromacs` to find available versions.

## Scripts

### submit_job.py

Universal submission script — software-agnostic.

**Usage**:
```
python submit_job.py \
  --input-dir <path> \
  --image <image_address> \
  --cmd "<run_command>" \
  [--machine c32_m128_cpu] \
  [--job-name my_job] \
  [--software cp2k] \
  [--disk-size 50]
```

**Arguments**:
- `--input-dir`: Directory containing all input files. Required.
- `--image`: Docker image address. Required.
- `--cmd`: Shell command to execute inside the container. Required.
- `--machine`: Bohrium machine type. Default: `c32_m128_cpu`.
- `--job-name`: Human-readable job name. Optional.
- `--software`: Software name label (metadata only). Optional.
- `--disk-size`: Disk size in GB. Default: 50.

**Output** (stdout JSON):
```json
{"success": true, "job_id": 12345, "bohr_job_id": 12345, "status": "Submitted"}
```

### poll_job.py

Universal monitor script — software-agnostic.

**Usage** (via `use_skill`, shown as tool call parameters):
```json
{
  "action": "run_script",
  "skill_name": "bohrium-job",
  "script_name": "poll_job.py",
  "script_args": "--job-id <id> [--result-dir results/run_xxx] [--max-polls 2880] [--poll-interval 30]",
  "script_timeout": 86400
}
```

> **CRITICAL**: `script_timeout` is a **`use_skill` tool parameter** — do NOT put it inside `script_args`. It controls the session-level process timeout, not a script CLI flag.

**`script_args` flags** (passed to `poll_job.py` command line):
- `--job-id`: Bohrium job id returned by `submit_job.py`. Required.
- `--result-dir`: Local directory to extract results into. Default: `results/run_<job_id>`.
- `--max-polls`: Maximum poll attempts. Default: 2880.
- `--poll-interval`: Seconds between polls. Default: 30.
- `--timeout-minutes`: Optional convenience shortcut. Computes `max_polls = max(1, timeout_minutes * 60 / poll_interval)` and overrides `--max-polls`. Use this instead of manually computing `--max-polls` when you want a wall-time cap (e.g. `--timeout-minutes 60` → 120 polls at 30 s).

**`script_timeout`** (`use_skill` parameter, not a script flag): Always set to `max_polls × poll_interval` (default **86400 s** = 2880 × 30). Without it the session kills the process after 60 s.

> ⚠️ **Do NOT reduce `--max-polls` below 2880** unless the user explicitly requests a shorter timeout. HPC jobs can take many hours; underestimating `--max-polls` will cause the poll loop to exhaust before the job finishes. When in doubt, always use the defaults: `--max-polls 2880`, `script_timeout: 86400`.

**Output** (stdout JSON — success):
```json
{"success": true, "job_id": 12345, "status": "Finished", "result_dir": "results/run_12345", "files": ["orca.out", "orca.gbw"], "polls_done": 42, "elapsed_seconds": 1260.0, "log_tail": "..."}
```

**Output** (stdout JSON — poll budget exhausted, job still running):
```json
{"success": true, "job_id": 12345, "status": "still_running", "polls_done": 120, "elapsed_seconds": 3600.0, "log_tail": "", "message": "Poll budget exhausted (120/120 polls, 60.0 min). Job is still running on Bohrium (job_id=12345). Re-invoke poll_job.py with the same --job-id to continue monitoring, or finish with task_completed=partial."}
```

> **`still_running` is NOT a failure.** `success: true` and exit code 0 mean the job is alive on Bohrium. See "Resuming Monitoring" below.

**Output** (stdout JSON — failed):
```json
{"success": false, "job_id": 12345, "status": "Failed", "result_dir": "results/run_12345", "files": [...], "polls_done": 5, "elapsed_seconds": 150.0, "log_tail": "<error from log file>", "error": "..."}
```

### Resuming Monitoring

When `poll_job.py` returns `status: "still_running"` (or `monitor_job` returns `status: "running"`):

1. **The job is alive on Bohrium** — this is not an error or timeout.
2. **Decision point** — choose one of:
   - **Re-poll** (preferred when session budget allows): re-invoke `poll_job.py` with the same `--job-id`. The script picks up where it left off (Bohrium tracks state, not the client).
   - **Yield with partial result**: call `finish` with `task_completed=partial`, report the `job_id`, and explain that the job is still running. The user can re-invoke the agent later to check.
3. **Do NOT start unrelated tasks** (literature search, structure building, etc.) while the job is pending.

### list_images.py

Query available Bohrium public Docker images.

**Usage**:
```
python list_images.py [--keyword <name>] [--max-results 20]
```

**Arguments**:
- `--keyword`: Filter images by name (case-insensitive). Optional.
- `--max-results`: Max number of results to return. Default: 20.

**Output** (stdout JSON):
```json
{
  "success": true,
  "total_found": 5,
  "returned": 5,
  "images": [{"id": 123, "name": "gromacs", "versions": [{"imageAddress": "registry.dp.tech/..."}]}]
}
```

### list_machines.py

Query available Bohrium machine types.

**Usage**:
```
python list_machines.py [--type cpu|gpu] [--keyword <name>] [--max-results 50]
```

**Arguments**:
- `--type`: `cpu` or `gpu`. Default: `cpu`.
- `--keyword`: Filter machines by name. Optional.
- `--max-results`: Max number of results to return. Default: 50.

**Output** (stdout JSON):
```json
{
  "success": true,
  "type": "cpu",
  "machines": [{"scassType": "c32_m128_cpu", "cpu": 32, "memory": 128}]
}
```

## When to use

- Input files have been prepared by `input-manual-helper` (CP2K, QE, ABINIT, LAMMPS, ORCA, GROMACS)
- User asks to submit / run / execute a computation on Bohrium HPC
- Any workflow needing split flow: submit and immediately return `job_id`, then monitor/download in a follow-up step
- When you need to discover available images or machine types before submission

## Rules

1. **Prepare gate**: If the user has already provided a complete, ready-to-run input file, skip `input-manual-helper` and submit directly. Otherwise, always call `input-manual-helper` first for CP2K / QE / ABINIT / LAMMPS / ORCA — do not hand-write input files here.
2. **Input files land in `oss_downloaded_files/`**, not in the `output_dir` you passed to `prepare_*`. Always `dir oss_downloaded_files` (Windows) or `ls oss_downloaded_files` to confirm the actual filename before constructing `--cmd`.
3. **Match MPI `-np` count** in `--cmd` to the machine's core count (e.g. 32 for `c32_m128_cpu`).
4. **Check `"success": true`** in JSON output before proceeding to the next step.
5. Run `submit_job.py` first, then pass the returned `job_id` into `poll_job.py`. **Never call the built-in `monitor_job` tool** — always use `use_skill bohrium-job run_script poll_job.py`.
6. **Log redirection must be unified**: in every software command, always use `> log 2>&1`. Do not use per-case filenames (for example `> orca.out`, `> qe.log`, `> caffeine.out`). This keeps `poll_job.py` log-tail behavior stable.
7. **Unknown software/version**: If the software is not in the Software Reference table, run `list_images.py --keyword <software>` to find the image, and `list_machines.py` to find a suitable machine type.

## Log Filename Best Practice

To avoid "job finished but log tail missing/wrong file" issues:

- Preferred (recommended now): enforce a single convention in all generated commands: `> log 2>&1`.
- Optional future hardening (if you want more flexibility): extend `poll_job.py` with a `--log-file` argument and pass the same filename used in `--cmd`.

For current workflows, the best solution is the first one: **standardize all commands to write to `log`**.
