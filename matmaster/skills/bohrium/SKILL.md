---
name: bohrium
description: "Bohrium HPC platform knowledge only. This skill is guidance-only and has no runnable scripts; all submit, poll, list_images, and list_machines operations must use the built-in Bohrium tool."
skill_type: operator
---

# Bohrium Platform Guide

This skill provides **platform knowledge only**.

- It is **guidance-only**
- It has **no runnable scripts**
- All actual operations (`submit`, `poll`, `download`, `list_images`, `list_machines`) must use the built-in `Bohrium` tool

## Workflow Pattern

1. Load a **software skill** (cp2k, qe, abacus, orca, lammps, gromacs, pyscf, abinit, pyatb) to get `image`, `machine`, and `cmd` parameters
2. `Bohrium(action="submit", input_dir="inputs/run_001", image="registry.dp.tech/dptech/abacus:LTSv3.10.1", cmd="OMP_NUM_THREADS=1 mpirun -np 16 abacus > log 2>&1", machine="c32_m128_cpu")`
3. `Bohrium(action="poll", job_id="job-123")` — returns current status immediately; the tool manages poll intervals automatically
4. `Bohrium(action="download", job_id="job-123", result_dir="results/run_job-123")`
5. Analyze `result_dir`, `files`, and `log_tail`

## Monitoring Strategy

The `poll` action is a **single-shot status check** that returns immediately:

| Status | Meaning | What to Do |
|--------|---------|------------|
| Pending / Scheduling | Job queued, waiting for resources | Do other work, poll again later |
| Running | Job executing on HPC node | Do other work, poll again later |
| Finished | Completed | Call `download` to fetch artifacts, then analyze output files and `log_tail` |
| Failed | Job crashed or errored | Call `download` to fetch logs and remaining artifacts for diagnosis |

**Throttle behavior**: The tool tracks polling frequency per job using an exponential backoff schedule (30s, 1m, 2m, 4m, ..., up to 1h). If you poll too soon, you receive a cached result with a `seconds_until_fresh` indicator. This is automatic — just call `poll` when needed and the tool manages the pacing.

**Batch jobs**: When you submit multiple jobs, poll them individually. Each job has its own throttle timer. You do not need to wait for all jobs before polling any of them.

## Credential Resolution

Bohrium credentials are resolved through the runtime handle with the following precedence:

1. **Explicit params** -- caller-provided values (e.g. passed directly to API functions)
2. **Runtime handle** -- `BohriumRuntimeHandle` attached to the session by the platform during a run (production path)
3. **Environment fallback** -- `BOHRIUM_ACCESS_KEY` / `BOHRIUM_PROJECT_ID` from `.env` or os.environ (local development)

In production, credentials come from the runtime handle automatically. Setting `BOHRIUM_ACCESS_KEY` in `.env` is the local development fallback for running without a platform session.

## Troubleshooting

| Symptom | Likely Cause | Resolution |
|---------|-------------|------------|
| submit returns `credentials unavailable` | No session and no env variable | In production: verify session has Bohrium credentials. In local dev: set `BOHRIUM_ACCESS_KEY` in `.env` |
| submit returns `project ID unavailable` | Missing or invalid project ID | In production: verify session credentials include project_id. In local dev: set `BOHRIUM_PROJECT_ID` in `.env` |
| Failed + empty log_tail | Command errored before writing to log | Check image/cmd compatibility; wrong binary path or missing dependencies |
| Failed + OOM in log | Insufficient machine memory | Switch to a larger machine (e.g. `c32_m128_cpu` → `c64_m256_cpu`) |
| Failed + "not enough slots" | MPI process count > available cores | Reduce `-np` to match machine core count |
| Finished but results empty | Output files not in expected locations | Check the command writes to the working directory (not a temp path) |
| `download` returns files but zip is missing | Sandbox result bundle not ready, but individual objects exist | The builtin tool falls back to per-object downloads; inspect `files` and `log_tail` in `result_dir` |

## Image / Machine Discovery

When a software skill's default image is outdated or the user needs a specific version:

- `Bohrium(action="list_images", keyword="<software>")` — find available Docker images
- `Bohrium(action="list_machines", machine_type="cpu")` — find CPU machine options
- `Bohrium(action="list_machines", machine_type="gpu", keyword="4090")` — find GPU options

Use `url` from image results as the `image` parameter, and `skuEnName` from machine results as the `machine` parameter.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `BOHRIUM_ACCESS_KEY` | local dev only | Bohrium API access key (production uses session credentials) |
| `BOHRIUM_PROJECT_ID` | local dev only | Bohrium project ID, positive integer (production uses session credentials) |
| `BOHRIUM_BASE_URL` | no | API base URL (default: `https://open.bohrium.com`) |
| `BOHRIUM_USE_SANDBOX` | no | `1` = sandbox API paths (default when unset); `0` = standard HPC paths |

## Sandbox vs Standard HPC

The API path set is determined by `BOHRIUM_USE_SANDBOX`:

- **Sandbox** (`1`, default when unset): uses `/openapi/v1/sandbox/job/...` paths. Suitable for testing and development.
- **Standard HPC** (`0`): uses `/openapi/v1/job/create`, `/openapi/v2/job/add`, `GET /openapi/v1/job/{id}`. For production workloads.

The `Bohrium` tool handles path selection automatically based on this environment variable.
