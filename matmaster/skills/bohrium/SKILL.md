---
name: bohrium
description: "Bohrium HPC platform operations: submit computational jobs, monitor status, download results, and query available Docker images and machine types. Software-agnostic — load the corresponding software skill (cp2k, qe, abacus, orca, lammps, gromacs, pyscf, abinit, pyatb) first to obtain image, machine, and command parameters before submitting."
skill_type: operator
---

# Bohrium Skill

Generic HPC job lifecycle management on the Bohrium platform. This skill handles **platform operations only** — it does not contain any software-specific knowledge (images, commands, physical checks).

> **Before submitting a job**, always load the corresponding software skill first (e.g. `cp2k`, `qe`, `abacus`, `orca`, `lammps`, `gromacs`, `pyscf`, `abinit`, `pyatb`) to obtain the correct `image`, `machine`, and `cmd` parameters.

## Operations

### 1. Submit a Job

Packages an input directory, uploads it, and creates a job on Bohrium. Returns `job_id` immediately — does NOT wait for completion.

```
run_script submit_job.py \
  --input-dir <path>       \
  --image <image_address>  \
  --cmd "<shell_command>"  \
  [--machine c32_m128_cpu] \
  [--job-name my_job]      \
  [--software <label>]     \
  [--disk-size 50]
```

**Parameters:**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `--input-dir` | yes | Directory containing all input files to upload |
| `--image` | yes | Docker image address (from software skill or `list_images.py`) |
| `--cmd` | yes | Shell command to execute inside the container |
| `--machine` | no | Bohrium machine type (default: `c32_m128_cpu`) |
| `--job-name` | no | Human-readable job name |
| `--software` | no | Software label (metadata only) |
| `--disk-size` | no | Disk size in GB (default: 50) |

**Output** (stdout JSON):
```json
{"success": true, "job_id": 12345, "bohr_job_id": 12345, "status": "Submitted", "use_sandbox": false}
```

**Rules:**
- `--cmd` **must** redirect output to a file named exactly `log`: append `> log 2>&1` to every command. Do not use custom log names.
- Match MPI `-np` count in `--cmd` to the machine's core count (e.g. 32 for `c32_m128_cpu`). Exception: ABACUS uses half the core count.
- Check `"success": true` in JSON output before proceeding to poll.

### 2. Poll / Monitor a Job

Polls a submitted job until it reaches a terminal state (Finished / Failed), then downloads and extracts results.

```json
{
  "action": "run_script",
  "skill": "bohrium",
  "script_name": "poll_job.py",
  "script_args": "--job-id <id> [--result-dir results/run_xxx] [--max-polls 2880] [--poll-interval 30]",
  "script_timeout": 86400
}
```

**Parameters:**

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `--job-id` | yes | — | Job ID returned by `submit_job.py` |
| `--result-dir` | no | `results/run_<job_id>` | Local directory for extracted results |
| `--max-polls` | no | 2880 | Maximum poll attempts |
| `--poll-interval` | no | 30 | Seconds between polls |
| `--timeout-minutes` | no | — | Convenience: sets max_polls = timeout_minutes * 60 / poll_interval |

> **CRITICAL**: `script_timeout` is a **skill tool parameter**, not part of `script_args`. Always set it to `max_polls × poll_interval` (default: 2880 × 30 = **86400 s**). Without it, the session default timeout (60 s) kills the script before the first poll completes.

> **Do NOT reduce `--max-polls` below 2880** unless the user explicitly requests a shorter timeout. HPC jobs can take many hours.

**Output** (success):
```json
{
  "success": true,
  "job_id": 12345,
  "status": "Finished",
  "result_dir": "results/run_12345",
  "files": ["output.log", "result.dat"],
  "polls_done": 42,
  "elapsed_seconds": 1260.0,
  "log_tail": "... last lines of log ..."
}
```

**Output** (still running — poll budget exhausted):
```json
{
  "success": true,
  "job_id": 12345,
  "status": "still_running",
  "polls_done": 120,
  "elapsed_seconds": 3600.0,
  "message": "Poll budget exhausted. Job is still running on Bohrium. Re-invoke poll_job.py to continue monitoring."
}
```

> `still_running` is **NOT a failure**. The job is alive on Bohrium. Re-invoke `poll_job.py` with the same `--job-id` to continue, or report `job_id` to the user for manual follow-up.

**Output** (failed):
```json
{
  "success": false,
  "job_id": 12345,
  "status": "Failed",
  "result_dir": "results/run_12345",
  "files": [...],
  "log_tail": "<error from log file>",
  "error": "Job failed on Bohrium"
}
```

### 3. List Available Docker Images

Query the Bohrium platform for available software images.

```
run_script list_images.py [--keyword <name>] [--max-results 20]
```

| Parameter | Description |
|-----------|-------------|
| `--keyword` | Filter by image name (case-insensitive), e.g. `--keyword gromacs` |
| `--max-results` | Max entries to return (default: 20) |

**Output:**
```json
{
  "success": true,
  "total_found": 5,
  "returned": 5,
  "images": [
    {
      "id": 123,
      "name": "gromacs",
      "versions": [
        {"url": "registry.dp.tech/dptech/gromacs:2022.2", "version": "2022.2", "resourceType": "CPU"}
      ]
    }
  ]
}
```

Use the `url` value from `versions` as the `--image` argument to `submit_job.py`.

### 4. List Available Machine Types

Query Bohrium for available machine specifications.

```
run_script list_machines.py [--type cpu|gpu] [--keyword <name>] [--max-results 50]
```

| Parameter | Description |
|-----------|-------------|
| `--type` | `cpu` or `gpu` (default: `cpu`) |
| `--keyword` | Filter by machine name, e.g. `--keyword c32` |
| `--max-results` | Max entries to return (default: 50) |

**Output:**
```json
{
  "success": true,
  "type": "cpu",
  "machines": [
    {"skuEnName": "c32_m128_cpu", "cpuCoreNum": 32, "memory": 128, "price": 2.56, "hasStock": true}
  ]
}
```

Use `skuEnName` as the `--machine` argument to `submit_job.py`.

## Sandbox vs Standard HPC

Submission and polling paths are controlled by the **environment variable** `BOHRIUM_USE_SANDBOX`:

| Value | API Paths | Description |
|-------|-----------|-------------|
| `0` or unset | `/openapi/v1/job/create`, `/openapi/v2/job/add`, `GET /openapi/v1/job/{id}` | **Standard HPC** (default) |
| `1` | `/openapi/v1/sandbox/job/...` | Sandbox mode |

This is an environment variable, not a script argument. Submit and poll **must** share the same setting, otherwise the job will not be visible.

## Typical Workflow

```
1. Load software skill     →  use_skill(cp2k)  →  get image, machine, cmd
2. Prepare input files     →  (as instructed by software skill)
3. Submit                  →  bohrium run_script submit_job.py --input-dir ... --image ... --cmd ...
4. Poll until completion   →  bohrium run_script poll_job.py --job-id ...
5. Analyze results         →  inspect result_dir files and log_tail
```

## When to Use

- Submitting any computation to Bohrium HPC
- Monitoring a previously submitted job
- Discovering available images or machine types before submission
- Any workflow that needs the split pattern: submit → get `job_id` → poll later

## Environment Requirements

| Variable | Required | Description |
|----------|----------|-------------|
| `BOHRIUM_ACCESS_KEY` | yes | Bohrium API access key |
| `BOHRIUM_PROJECT_ID` | yes | Bohrium project ID (positive integer) |
| `BOHRIUM_BASE_URL` | no | API base URL (default: `https://open.bohrium.com`) |
| `BOHRIUM_USE_SANDBOX` | no | `1` for sandbox mode (default: standard HPC) |

## Scripts

| Script | Purpose | Blocking |
|--------|---------|----------|
| `submit_job.py` | 3-step job creation (create → upload → add) | No (returns immediately) |
| `poll_job.py` | Poll until terminal state, download results | Yes (long-running, needs `script_timeout`) |
| `list_images.py` | Query available Docker images | No |
| `list_machines.py` | Query available machine types | No |
